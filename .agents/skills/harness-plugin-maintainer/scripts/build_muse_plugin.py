#!/usr/bin/env python3
"""Build the deterministic harness-kit-muse plugin runtime.

The Muse plugin ships the same canonical user skills as ``harness-kit``,
adapted to the Muse native spec: shared-skill frontmatter must not
pre-approve shell execution, so the ``allowed-tools`` frontmatter key is
removed from the shipped copies (canonical sources keep it). Muse-specific
files (``.muse-plugin/plugin.json``, ``hooks/``, ``prototype/``) are
hand-maintained in place; this script regenerates only ``skills/`` from the
canonical tree, refreshes the vendored guard, and writes the manifest,
archive, and release evidence deterministically.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

from build_plugin import check_user_skill_inventory, normalize_text_payload
from plugin_common import (
    GENERATED_BY,
    copy_tree_clean,
    ensure_no_symlink,
    load_json,
    repo_root,
    retry_filesystem,
    sha256_file,
    tree_manifest,
    write_json,
)

MUSE_PLUGIN_ID = "harness-kit-muse"
MUSE_PLUGIN_VERSION = "0.2.0"
MUSE_PLUGIN_ROOT_REL = Path("plugins") / MUSE_PLUGIN_ID
MUSE_MANIFEST_REL = MUSE_PLUGIN_ROOT_REL / "MANIFEST.sha256.json"
MUSE_RELEASE_REL = Path("maintainer") / "plugin" / "release-muse.json"
STRIPPED_FRONTMATTER_KEYS = ("allowed-tools",)
GUARD_ASSET_REL = Path("skills") / "project-write-access" / "assets" / "runtime" / "write_access_guard.py"
STATIC_DIRS = (".muse-plugin", "hooks", "prototype")


def strip_frontmatter(text: str) -> str:
    """Remove Muse-inapplicable keys from a SKILL.md frontmatter block.

    Only the leading ``---`` block is touched; body content (including fenced
    code that may mention the same words) is preserved byte-for-byte.
    """
    lines = text.split("\n")
    if len(lines) < 3 or lines[0].strip() != "---":
        return text
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return text
    kept = [line for line in lines[1:end] if line.split(":")[0].strip() not in STRIPPED_FRONTMATTER_KEYS]
    return "\n".join([lines[0], *kept, *lines[end:]])


def copy_muse_skills(root: Path, plugin_root: Path, capabilities: dict) -> None:
    skills_root = plugin_root / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for skill in capabilities["logical_user_skills"]:
        copy_tree_clean(root / "skills" / skill, skills_root / skill)
    for skill_file in sorted(skills_root.rglob("SKILL.md")):
        original = skill_file.read_text(encoding="utf-8")
        stripped = strip_frontmatter(original)
        if stripped != original:
            skill_file.write_text(stripped, encoding="utf-8", newline="\n")


def refresh_static_sources(root: Path, plugin_root: Path) -> None:
    """Copy hand-maintained Muse sources and refresh the vendored guard.

    Static Muse files live in the tracked plugin tree so they stay reviewable
    in place; the guard is vendored from its canonical asset on every build so
    a guard fix cannot silently miss the Muse runtime.
    """
    canonical_plugin = root / MUSE_PLUGIN_ROOT_REL
    for name in STATIC_DIRS:
        source = canonical_plugin / name
        if not source.is_dir():
            raise RuntimeError(f"hand-maintained Muse source missing: {source}")
        copy_tree_clean(source, plugin_root / name)
    guard_source = root / GUARD_ASSET_REL
    guard_target = plugin_root / "hooks" / "write-access-guard.py"
    if not guard_source.is_file():
        raise RuntimeError(f"canonical guard asset missing: {guard_source}")
    guard_target.parent.mkdir(parents=True, exist_ok=True)
    retry_filesystem(lambda: shutil.copyfile(guard_source, guard_target))


def validate_muse_plugin(root: Path, plugin_root: Path, capabilities: dict) -> None:
    manifest = load_json(plugin_root / ".muse-plugin" / "plugin.json")
    if manifest.get("name") != MUSE_PLUGIN_ID:
        raise RuntimeError(f"muse plugin id drifted: {manifest.get('name')}")
    if manifest.get("version") != MUSE_PLUGIN_VERSION:
        raise RuntimeError(
            f"muse plugin version {manifest.get('version')} != {MUSE_PLUGIN_VERSION}"
        )
    declared = [item["id"] for item in manifest.get("capabilities", {}).get("skills", [])]
    if sorted(declared) != sorted(capabilities["logical_user_skills"]):
        raise RuntimeError("muse plugin capabilities differ from logical user skills")
    for item in manifest.get("capabilities", {}).get("skills", []):
        if not (plugin_root / item["path"]).is_file():
            raise RuntimeError(f"muse plugin capability points at a missing file: {item['path']}")
    for skill in capabilities["logical_user_skills"]:
        skill_file = plugin_root / "skills" / skill / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"muse plugin is missing skill: {skill}")
        frontmatter = skill_file.read_text(encoding="utf-8").split("---")
        if len(frontmatter) >= 3 and any(
            line.split(":")[0].strip() in STRIPPED_FRONTMATTER_KEYS
            for line in frontmatter[1].split("\n")
        ):
            raise RuntimeError(f"muse plugin ships a stripped frontmatter key: {skill_file}")
    for hook in manifest.get("capabilities", {}).get("hooks", []):
        for command in hook.get("command", []):
            candidate = plugin_root / command
            if command.endswith(".py") and not candidate.is_file():
                raise RuntimeError(f"muse plugin hook points at a missing file: {command}")
    ensure_no_symlink(plugin_root)


def write_muse_archive(plugin_root: Path) -> Path:
    archive = plugin_root.parent / f"{MUSE_PLUGIN_ID}-{MUSE_PLUGIN_VERSION}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    fixed_dt = (2026, 7, 29, 0, 0, 0)
    fd, tmp_name = tempfile.mkstemp(dir=archive.parent, prefix=f".{archive.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            files = sorted(
                (path for path in plugin_root.rglob("*") if path.is_file()),
                key=lambda path: path.relative_to(plugin_root).as_posix(),
            )
            for path in files:
                info = zipfile.ZipInfo(str(path.relative_to(plugin_root)).replace("\\", "/"), fixed_dt)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                info._compresslevel = 9
                zf.writestr(info, path.read_bytes())
        retry_filesystem(lambda: os.replace(tmp, archive))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return archive


def reset_output(plugin_root: Path, output_root: Path) -> None:
    resolved_root = output_root.resolve()
    resolved_plugin = plugin_root.resolve()
    try:
        resolved_plugin.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"refusing to reset muse plugin outside output root: {plugin_root}") from exc
    if resolved_plugin == resolved_root:
        raise RuntimeError(f"refusing to reset output root itself: {plugin_root}")
    if plugin_root.exists():
        retry_filesystem(lambda: shutil.rmtree(plugin_root))


def build(root: Path, output_root: Path | None = None) -> dict:
    target_root = output_root if output_root is not None else root
    final_root = target_root / MUSE_PLUGIN_ROOT_REL

    capabilities = load_json(root / "maintainer" / "plugin" / "CAPABILITIES.json")
    check_user_skill_inventory(root, capabilities)

    staging_root = final_root.with_name(final_root.name + ".building")
    reset_output(staging_root, target_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        copy_muse_skills(root, staging_root, capabilities)
        refresh_static_sources(root, staging_root)
        normalize_text_payload(staging_root)
        validate_muse_plugin(root, staging_root, capabilities)

        artifact_manifest = tree_manifest(staging_root)
        write_json(staging_root / "MANIFEST.sha256.json", {"generated_by": GENERATED_BY, "files": artifact_manifest})
        archive = write_muse_archive(staging_root)

        release = {
            "schema_version": "1.0.0",
            "generated_by": GENERATED_BY,
            "plugin_id": MUSE_PLUGIN_ID,
            "version": MUSE_PLUGIN_VERSION,
            "plugin_root": str(MUSE_PLUGIN_ROOT_REL).replace("\\", "/"),
            "archive": str(archive.relative_to(target_root)).replace("\\", "/"),
            "archive_sha256": sha256_file(archive),
            "logical_user_skills": len(capabilities["logical_user_skills"]),
            "physical_skills": len(capabilities["logical_user_skills"]),
            "released_state_preserved": True,
            "push_tag_release_created": False,
        }
        ensure_no_symlink(staging_root)

        if staging_root != final_root:
            reset_output(final_root, target_root)
            retry_filesystem(lambda: staging_root.rename(final_root))
        write_json(target_root / MUSE_RELEASE_REL, release)
        return release
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def compare_tree(expected_root: Path, actual_root: Path) -> list[str]:
    expected = {item["path"]: item["sha256"] for item in tree_manifest(expected_root)}
    actual = {item["path"]: item["sha256"] for item in tree_manifest(actual_root)} if actual_root.is_dir() else {}
    messages: list[str] = []
    for path in sorted(expected.keys() - actual.keys()):
        messages.append(f"missing generated muse plugin file: {path}")
    for path in sorted(actual.keys() - expected.keys()):
        messages.append(f"unexpected generated muse plugin file: {path}")
    for path in sorted(expected.keys() & actual.keys()):
        if expected[path] != actual[path]:
            messages.append(f"changed generated muse plugin file: {path}")
    return messages


def compare_file(expected: Path, actual: Path, label: str) -> list[str]:
    if not actual.is_file():
        return [f"missing generated {label}: {actual}"]
    if expected.read_bytes() != actual.read_bytes():
        return [f"changed generated {label}: {actual}"]
    return []


def check(root: Path) -> int:
    messages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="harness-kit-muse-build-check-") as tmp:
        expected_root = Path(tmp)
        release = build(root, output_root=expected_root)
        messages.extend(compare_tree(expected_root / MUSE_PLUGIN_ROOT_REL, root / MUSE_PLUGIN_ROOT_REL))
        messages.extend(
            compare_file(
                expected_root / release["archive"],
                root / release["archive"],
                "muse plugin archive",
            )
        )
        messages.extend(
            compare_file(
                expected_root / MUSE_RELEASE_REL,
                root / MUSE_RELEASE_REL,
                "muse release metadata",
            )
        )
    if messages:
        for message in messages:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1
    print("muse plugin build check passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = repo_root()
    if args.check:
        return check(root)
    release = build(root)
    print(json.dumps(release, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

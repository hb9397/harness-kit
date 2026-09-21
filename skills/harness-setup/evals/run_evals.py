"""Contract and executable filesystem fixture checks for project setup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2]
SETUP_ROOT = SKILLS_ROOT / "harness-setup"
BOOTSTRAP_SKILL = SKILLS_ROOT / "harness-bootstrap" / "SKILL.md"
CONTEXT_SKILL = SKILLS_ROOT / "context-doc" / "SKILL.md"
REPO_ROOT = SKILLS_ROOT.parent
PORTABLE_BASELINE_FIXTURE = SETUP_ROOT / "evals" / "fixtures" / "portable-routing-baseline.json"
PORTABLE_ROUTING_TEMPLATE_ROOT = SETUP_ROOT / "templates" / "portable-routing"
PORTABLE_ROUTING_FIXTURE = SETUP_ROOT / "evals" / "fixtures" / "portable-routing-bundle.json"
ROUTING_COVERAGE_MANIFEST = SETUP_ROOT / "evals" / "fixtures" / "portable-routing-coverage.json"
# Windows PowerShell 5.1 is the configured host shell, but Linux CI only has pwsh.
HOOK_SHELLS = tuple(shell for shell in ("pwsh", "powershell.exe") if shutil.which(shell))

FORBIDDEN_LOCAL_SKILL_PATHS = (
    ".agents/skills",
    ".claude/skills",
    "skills/",
)
MARKDOWN_MARKERS = (
    "<!-- harness-kit:managed:start -->",
    "<!-- harness-kit:managed:end -->",
)
GITIGNORE_MARKERS = (
    "# harness-kit:managed:start",
    "# harness-kit:managed:end",
)
CLAUDE_INSTRUCTION_PATHS = (
    Path("CLAUDE.md"),
    Path(".claude") / "CLAUDE.md",
    Path("CLAUDE.local.md"),
)
LEGACY_MANAGED_BRIDGE = """<!-- harness-kit:managed:start -->
@AGENTS.md

## Claude Code 전용 차이

- portable routing과 Claude host hook 상태는 `@.ai-docs/harness/artifact-routing.json`을 확인한다.
- bundle 생성만으로 Claude 설정·hook을 활성화하지 않는다.
<!-- harness-kit:managed:end -->
"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(text: str, needle: str, source: Path) -> None:
    if needle not in text:
        raise AssertionError(f"{source}: missing required contract: {needle}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_fingerprint(project: Path, artifacts: list[Path]) -> tuple[str, list[dict[str, str]]]:
    ledger = project / ".ai-docs" / ".harness" / "humanize-handoffs.json"
    normalized: list[dict[str, str]] = []
    for path in artifacts:
        if path.resolve() == ledger.resolve():
            raise AssertionError("ledger must not be part of the humanize artifact bundle")
        normalized.append(
            {
                "path": path.relative_to(project).as_posix(),
                "sha256": sha256(path),
            }
        )
    normalized.sort(key=lambda item: item["path"])
    canonical = "\n".join(
        f"{item['path']}\0{item['sha256']}" for item in normalized
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), normalized


def append_ledger_event(
    ledger: Path,
    fingerprint: str,
    artifacts: list[dict[str, str]],
    status: str,
    supersedes_fingerprint: str | None = None,
) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.loads(ledger.read_text(encoding="utf-8"))
        if ledger.exists()
        else {"schema_version": "1.0.0", "records": []}
    )
    record = next(
        (
            item
            for item in payload["records"]
            if item["artifact_fingerprint"] == fingerprint
        ),
        None,
    )
    if record is None:
        record = {
            "artifact_fingerprint": fingerprint,
            "producer": "harness-setup",
            "artifact_bundle_id": "fixture-correlation-id",
            "profile": "document-refinement",
            "artifacts": artifacts,
            "events": [],
        }
        if supersedes_fingerprint is not None:
            record["supersedes_fingerprint"] = supersedes_fingerprint
        payload["records"].append(record)
    record["events"].append(
        {"status": status, "recorded_at": "2026-07-30T00:00:00+09:00"}
    )
    temp_path = ledger.with_name(f".{ledger.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, ledger)


def snapshot_files(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def detect_mode(project: Path) -> str:
    canonical = (project / ".ai-docs").exists()
    legacy = (project / ".docs").exists()
    if canonical and legacy:
        return "document-root-conflict"
    if legacy:
        return "legacy-document-root-migration"
    return "update" if canonical or (project / "AGENTS.md").exists() else "initial"


def find_claude_instruction_files(project: Path, stop_at: Path | None = None) -> list[Path]:
    """Return project/ancestor Claude instruction files that preempt default AGENTS loading."""
    current = project.resolve()
    boundary = stop_at.resolve() if stop_at is not None else None
    found: list[Path] = []
    while True:
        found.extend(path for relative in CLAUDE_INSTRUCTION_PATHS if (path := current / relative).is_file())
        if current == boundary or current.parent == current:
            return found
        current = current.parent


def is_managed_only_claude_bridge(path: Path) -> bool:
    if not path.is_file():
        return False
    text = read(path)
    start, end = MARKDOWN_MARKERS
    if text.count(start) != 1 or text.count(end) != 1 or "@AGENTS.md" not in text:
        return False
    start_index = text.index(start)
    end_index = text.index(end, start_index) + len(end)
    return not (text[:start_index] + text[end_index:]).strip()


def replace_managed_block(existing: str, template: str, markers: tuple[str, str]) -> str:
    start, end = markers
    if existing.count(start) != 1 or existing.count(end) != 1:
        raise ValueError("existing file is unmanaged or has malformed markers")
    if template.count(start) != 1 or template.count(end) != 1:
        raise ValueError("template must contain exactly one managed block")
    existing_start = existing.index(start)
    existing_end = existing.index(end, existing_start) + len(end)
    template_start = template.index(start)
    template_end = template.index(end, template_start) + len(end)
    return (
        existing[:existing_start]
        + template[template_start:template_end]
        + existing[existing_end:]
    )


def render(template_name: str, replacements: dict[str, str] | None = None) -> str:
    text = read(SETUP_ROOT / "templates" / template_name)
    for key, value in (replacements or {}).items():
        text = text.replace(key, value)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", text):
        raise AssertionError(f"unresolved placeholder in {template_name}")
    return text


def render_portable(template_name: str, replacements: dict[str, str]) -> str:
    """Render an approved portable-routing template without host installation."""
    template = PORTABLE_ROUTING_TEMPLATE_ROOT / template_name
    text = read(template)
    for key, value in replacements.items():
        text = text.replace(key, value)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", text):
        raise AssertionError(f"unresolved portable-routing placeholder in {template}")
    return text


CODEX_PRE_TOOL_USE_TOP_LEVEL = {"continue", "decision", "hookSpecificOutput", "reason", "stopReason", "suppressOutput", "systemMessage"}
CODEX_PRE_TOOL_USE_SPECIFIC = {"additionalContext", "hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput"}
# Tool names that Codex 0.154.0 exposes to PreToolUse without writing project files.
CODEX_READ_ONLY_TOOL_NAMES = ("update_plan", "view_image", "tool_search", "mcp__filesystem__read_file", "spawn_agent", "Read", "Grep")


def codex_matcher_matches(matcher: str, tool_name: str) -> bool:
    """Mirror codex-rs/hooks/src/events/common.rs matches_matcher, including tool aliases."""
    inputs = [tool_name] + {"apply_patch": ["Write", "Edit"], "spawn_agent": ["Agent"]}.get(tool_name, [])
    if matcher in {"", "*"}:
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        return any(candidate in matcher.split("|") for candidate in inputs)
    return any(re.search(matcher, candidate) for candidate in inputs)


def codex_pre_tool_use_outcome(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    """Mirror codex-rs/hooks/src/events/pre_tool_use.rs parse_completed for rust-v0.154.0.

    Returns (completed|warning|blocked, reason) and raises for every output Codex marks Failed.
    """
    # Source accepts exit 2 + stderr as a block, but the Codex CLI 0.154.0 Windows smoke test
    # continued the tool call, so the adapter must fail closed through the exit 0 deny response.
    if returncode != 0:
        raise AssertionError(f"hook exited with code {returncode}: {stderr}")
    trimmed = stdout.strip()
    if not trimmed:
        return "completed", ""
    try:
        wire = json.loads(trimmed)
    except json.JSONDecodeError:
        wire = None
    if not isinstance(wire, dict):
        if trimmed.startswith(("{", "[")):
            raise AssertionError(f"hook returned invalid pre-tool-use JSON output: {stdout}")
        return "completed", ""
    specific = wire.get("hookSpecificOutput")
    if set(wire) - CODEX_PRE_TOOL_USE_TOP_LEVEL or (
        specific is not None
        and (not isinstance(specific, dict) or set(specific) - CODEX_PRE_TOOL_USE_SPECIFIC or specific.get("hookEventName") != "PreToolUse")
    ):
        raise AssertionError(f"hook returned invalid pre-tool-use JSON output: {stdout}")
    if wire.get("continue") is False or "stopReason" in wire or wire.get("suppressOutput"):
        raise AssertionError(f"PreToolUse hook returned unsupported universal field: {stdout}")
    if specific and any(key in specific for key in ("permissionDecision", "permissionDecisionReason", "updatedInput")):
        decision = specific.get("permissionDecision")
        if decision == "deny" and str(specific.get("permissionDecisionReason") or "").strip():
            return "blocked", specific["permissionDecisionReason"]
        if decision == "allow" and "updatedInput" in specific:
            return "completed", ""
        raise AssertionError(f"PreToolUse hook returned unsupported hookSpecificOutput: {stdout}")
    if "decision" in wire or "reason" in wire:
        if wire.get("decision") == "block" and str(wire.get("reason") or "").strip():
            return "blocked", wire["reason"]
        raise AssertionError(f"PreToolUse hook returned unsupported legacy decision: {stdout}")
    return ("warning", wire["systemMessage"]) if "systemMessage" in wire else ("completed", "")


def apply_patch_payload(*files: tuple[str, str]) -> dict:
    lines = ["*** Begin Patch"]
    for path, content in files:
        lines += [f"*** Add File: {path}", f"+{content}"]
    lines.append("*** End Patch")
    return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_use_id": "call-1", "tool_input": {"command": "\n".join(lines)}}


def check_portable_routing_bundle() -> None:
    """B1 contract: a copied project-owned bundle remains interpretable offline."""
    fixture = json.loads(read(PORTABLE_ROUTING_FIXTURE))
    replacements = fixture["replacements"]
    required_templates = (
        "artifact-routing.json.template",
        "artifact-format-contract.json.template",
        "README.md.template",
        "install-routing.ps1.template",
        "normalize-artifact.ps1.template",
        "hooks/artifact-route-core.ps1.template",
        "hooks/claude-pre-tool-use.ps1.template",
        "hooks/codex-pre-tool-use.ps1.template",
        "hooks/approve-artifact.ps1.template",
        "claude-settings-hook.json.template",
        "codex-hooks.json.template",
        "inbox-artifact-manifest.json.template",
    )
    for template_name in required_templates:
        if not (PORTABLE_ROUTING_TEMPLATE_ROOT / template_name).is_file():
            raise AssertionError(f"missing portable-routing template: {template_name}")

    routing = json.loads(render_portable("artifact-routing.json.template", replacements))
    if routing["schema_version"] != "1.2.0":
        raise AssertionError("portable routing schema version drifted")
    if routing["mode"] not in {"single", "multi"}:
        raise AssertionError("portable routing mode must be single or multi")
    if routing["project_root"] != ".":
        raise AssertionError("shared routing must use a checkout-relative project root")
    if routing.get("setup", {}).get("local_state_path") != ".ai-docs/.harness/routing-state.local.json":
        raise AssertionError("host state must be stored in the ignored local state file")
    for host, item in routing["hosts"].items():
        if set(item) != {"layer", "config_path", "adapter_path"}:
            raise AssertionError(f"shared routing leaked local host state for {host}: {set(item)}")
    repository = routing.get("repositories", [None])[0]
    if repository != {
        "id": "app-source",
        "provider": "github",
        "host": "github.com",
        "owner": "fixture",
        "name": "application",
        "purpose": "source",
        "applications": ["application"],
    }:
        raise AssertionError("routing manifest lost provider repository-to-application mapping")
    rendered_routing = json.dumps(routing)
    if re.search(r"(?i)(?:[a-z]:[/\\\\](?:users|home)[/\\\\]|/(?:home|users)/)", rendered_routing):
        raise AssertionError("portable routing schema stores a user-specific path")

    for template_name in ("artifact-format-contract.json.template", "inbox-artifact-manifest.json.template"):
        json.loads(render_portable(template_name, replacements))

    claude_hook_config = json.loads(render_portable("claude-settings-hook.json.template", replacements))
    claude_handler = claude_hook_config["hooks"]["PreToolUse"][0]["hooks"][0]
    if claude_handler.get("command") != "powershell.exe" or "${CLAUDE_PROJECT_DIR}/.claude/hooks/claude-pre-tool-use.ps1" not in claude_handler.get("args", []):
        raise AssertionError("Claude Windows hook config must use the documented exec form")

    codex_hook_template = read(PORTABLE_ROUTING_TEMPLATE_ROOT / "codex-hooks.json.template")
    if "{{CODEX_HOOK_PATH}}" in codex_hook_template:
        raise AssertionError("Codex hook command still requires an installation-time absolute path")
    codex_hook_config = json.loads(render_portable("codex-hooks.json.template", replacements))
    codex_handler = codex_hook_config["hooks"]["PreToolUse"][0]["hooks"][0]
    if "(Get-Location).Path" not in codex_handler.get("commandWindows", "") or ".codex/hooks/codex-pre-tool-use.ps1" not in codex_handler.get("commandWindows", ""):
        raise AssertionError("Codex Windows hook config does not resolve the project hook portably")
    codex_matcher = codex_hook_config["hooks"]["PreToolUse"][0]["matcher"]
    for write_tool in ("apply_patch", "Bash"):
        if not codex_matcher_matches(codex_matcher, write_tool):
            raise AssertionError(f"Codex matcher must include write-capable tool: {write_tool}")
    for read_tool in CODEX_READ_ONLY_TOOL_NAMES:
        if codex_matcher_matches(codex_matcher, read_tool):
            raise AssertionError(f"Codex matcher must not run the write guard for read-only tool: {read_tool}")
    if f"matcher = '{codex_matcher}'" not in read(PORTABLE_ROUTING_TEMPLATE_ROOT / "install-routing.ps1.template"):
        raise AssertionError("install-routing Codex matcher drifted from codex-hooks.json.template")

    claude_adapter = render_portable("hooks/claude-pre-tool-use.ps1.template", replacements)
    codex_adapter = render_portable("hooks/codex-pre-tool-use.ps1.template", replacements)
    if "$env:CLAUDE_PROJECT_DIR" not in claude_adapter or "tool_name" not in claude_adapter:
        raise AssertionError("Claude adapter no longer validates Claude PreToolUse input")
    if "Join-Path $env:CLAUDE_PROJECT_DIR '.ai-docs/harness/hooks/artifact-route-core.ps1'" not in claude_adapter:
        raise AssertionError("Claude adapter must use the shared project-owned core")
    if "Codex tool_input.command payload cannot be used" not in claude_adapter or "exit 2" not in claude_adapter:
        raise AssertionError("Claude adapter no longer rejects the Codex command input")
    if "tool_input.command" not in codex_adapter or "apply_patch" not in codex_adapter:
        raise AssertionError("Codex adapter no longer validates Codex PreToolUse input")
    if "Join-Path $projectRoot '.ai-docs/harness/hooks/artifact-route-core.ps1'" not in codex_adapter:
        raise AssertionError("Codex adapter must use the shared project-owned core")
    if "$env:CLAUDE_PROJECT_DIR" in codex_adapter:
        raise AssertionError("Codex adapter incorrectly accepts Claude project input")
    core = render_portable("hooks/artifact-route-core.ps1.template", replacements)
    for token in (
        "[IO.Path]::GetFullPath",
        "Test-Contained",
        "content_sha256",
        "one-shot approval marker",
        "dynamic shell target is outside reliable local-hook extraction",
    ):
        if token not in core:
            raise AssertionError(f"portable routing core is missing write-guard contract: {token}")
    if "hookSpecificOutput" not in codex_adapter or "hookEventName = 'PreToolUse'" not in codex_adapter:
        raise AssertionError("Codex adapter must emit the documented deny response")

    with tempfile.TemporaryDirectory(prefix="portable-routing-bundle-") as tmp:
        bundle = Path(tmp) / "bundle"
        bundle.mkdir()
        for template_name in required_templates:
            destination = bundle / template_name.removesuffix(".template")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                render_portable(template_name, replacements),
                encoding="utf-8",
                newline="\n",
            )
        readme = read(bundle / "README.md")
        if "harness-kit" in readme.lower():
            raise AssertionError("portable bundle documentation still requires Harness Kit")
        installer = read(bundle / "install-routing.ps1")
        for host in ("claude", "codex"):
            if f"{host}" not in installer.lower():
                raise AssertionError(f"portable installer does not plan {host} targets")
        if "-Plan" not in installer:
            raise AssertionError("portable installer has no no-write plan mode")
        normalizer = read(bundle / "normalize-artifact.ps1")
        for token in ("ApprovePromotion", "marker-aware", "fixed-format-inbox-only"):
            if token not in normalizer:
                raise AssertionError(f"portable normalizer contract missing: {token}")

    with tempfile.TemporaryDirectory(prefix="portable-routing-plan-") as tmp:
        project = Path(tmp) / "clean-project"
        bundle = project / ".ai-docs" / "harness"
        bundle.mkdir(parents=True)
        install_replacements = dict(replacements)
        for template_name in required_templates:
            destination = bundle / template_name.removesuffix(".template")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                render_portable(template_name, install_replacements),
                encoding="utf-8",
                newline="\n",
            )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Plan"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"portable installer plan failed: {result.stderr}")
        plan = json.loads(result.stdout)
        if plan["project_root"] != ".":
            raise AssertionError("portable installer output leaked the resolved checkout root")
        targets = {item["host"]: item for item in plan["targets"]}
        if set(targets) != {"claude", "codex"}:
            raise AssertionError("portable installer plan does not contain both host targets")
        if targets["claude"]["trust"] != "pending-trust" or targets["codex"]["trust"] != "pending-trust":
            raise AssertionError("portable installer plan must not activate host trust")
        if any(Path(item["config"]).is_absolute() or Path(item["adapter"]).is_absolute() for item in targets.values()):
            raise AssertionError("portable installer plan exposed host absolute paths")

        claude_settings = project / ".claude" / "settings.json"
        claude_settings.parent.mkdir(parents=True, exist_ok=True)
        claude_settings.write_text('{"permissions":{"allow":["Read"]}}\n', encoding="utf-8")
        before_apply = snapshot_files(project)
        shared_routing_before = (bundle / "artifact-routing.json").read_bytes()
        denied = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Apply"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if denied.returncode == 0 or snapshot_files(project) != before_apply:
            raise AssertionError("unapproved host Apply must fail without writing")
        applied = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Apply", "-ApproveHostInstall"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if applied.returncode != 0:
            raise AssertionError(f"approved portable installer apply failed: {applied.stderr}")
        if not (project / ".claude" / "hooks" / "claude-pre-tool-use.ps1").is_file() or not (project / ".codex" / "hooks" / "codex-pre-tool-use.ps1").is_file():
            raise AssertionError("approved Apply did not materialize both host adapters")
        if "Read" not in read(claude_settings):
            raise AssertionError("approved Apply did not preserve unrelated Claude settings")
        if (bundle / "artifact-routing.json").read_bytes() != shared_routing_before:
            raise AssertionError("host installation mutated the shared routing contract")
        local_state_path = project / ".ai-docs" / ".harness" / "routing-state.local.json"
        if not local_state_path.is_file():
            raise AssertionError("approved Apply did not create local-only host state")

        codex_adapter_path = project / ".codex" / "hooks" / "codex-pre-tool-use.ps1"

        def invoke_codex(payload: dict | str, shell: str = "pwsh") -> tuple[str, str]:
            result = subprocess.run(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(codex_adapter_path)],
                input=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return codex_pre_tool_use_outcome(result.returncode, result.stdout, result.stderr)

        nested_cwd = project / "src" / "nested"
        nested_cwd.mkdir(parents=True)
        codex_config = json.loads(read(project / ".codex" / "hooks.json"))
        portable_command = codex_config["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]
        portable_run = subprocess.run(
            portable_command,
            shell=True,
            cwd=nested_cwd,
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "Get-ChildItem .ai-docs"}}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if codex_pre_tool_use_outcome(portable_run.returncode, portable_run.stdout, portable_run.stderr) != ("completed", ""):
            raise AssertionError(f"portable Codex command failed from a nested checkout path: {portable_run.stderr}")

        outside = invoke_codex(apply_patch_payload(("../escape.md", "blocked")))
        if outside[0] != "blocked" or "outside project containment" not in outside[1]:
            raise AssertionError(f"Codex traversal patch was not denied: {outside}")
        superpowers = invoke_codex(apply_patch_payload(("docs/superpowers/plans/external.md", "blocked")))
        if superpowers[0] != "blocked" or ".ai-docs/instruction/artifact-output-routing-instruction.md" not in superpowers[1]:
            raise AssertionError(f"Superpowers default path was not redirected to canonical routing: {superpowers}")
        moved = apply_patch_payload()
        moved["tool_input"]["command"] = "*** Begin Patch\n*** Update File: README.md\n*** Move to: ../moved.md\n*** End Patch"
        if invoke_codex(moved)[0] != "blocked":
            raise AssertionError("Codex apply_patch Move to target escaped the write guard")

        existing = project / ".ai-docs" / "instruction" / "existing.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("existing\n", encoding="utf-8")
        existing_patch = apply_patch_payload((".ai-docs/instruction/existing.md", "revised"))
        if invoke_codex(existing_patch) != ("completed", ""):
            raise AssertionError("existing canonical patch was not allowed as a Codex no-op")
        for shell in HOOK_SHELLS:
            if invoke_codex(existing_patch, shell=shell) != ("completed", ""):
                raise AssertionError(f"Codex existing canonical patch was not allowed under {shell}")
        if invoke_codex({"tool_name": "Bash", "tool_input": {"command": "Get-ChildItem .ai-docs"}}) != ("completed", ""):
            raise AssertionError("Codex read-only shell command was not a no-op")
        bypass = invoke_codex({"tool_name": "Bash", "tool_input": {"command": "Set-Content $(Get-Target) x"}})
        if bypass[0] != "warning" or "bypass" not in bypass[1]:
            raise AssertionError(f"Codex dynamic shell bypass was not a schema-valid warning: {bypass}")
        for invalid_payload, label in (
            ("", "empty payload"),
            ("{not json", "malformed payload"),
            ({"tool_name": "apply_patch"}, "missing tool_input"),
            ({"tool_name": "apply_patch", "tool_input": {"patch": "*** Begin Patch"}}, "missing command"),
        ):
            for shell in HOOK_SHELLS:
                invalid = invoke_codex(invalid_payload, shell=shell)
                if invalid[0] != "blocked" or "Codex routing guard failed" not in invalid[1]:
                    raise AssertionError(f"Codex {label} did not fail closed with a deny response under {shell}: {invalid}")
        routing_path = bundle / "artifact-routing.json"
        routing_backup = routing_path.read_bytes()
        routing_path.write_text("{broken", encoding="utf-8")
        try:
            core_failure = invoke_codex(existing_patch)
        finally:
            routing_path.write_bytes(routing_backup)
        if core_failure[0] != "blocked" or "Codex routing guard failed" not in core_failure[1]:
            raise AssertionError(f"Codex core exception did not fail closed with a deny response: {core_failure}")

        proposed_content = "approved new instruction"
        new_payload = apply_patch_payload((".ai-docs/instruction/new.md", proposed_content))
        new_result = invoke_codex(new_payload)
        if new_result[0] != "blocked" or "one-shot approval marker" not in new_result[1]:
            raise AssertionError(f"new managed artifact without marker was not denied: {new_result}")
        approval = subprocess.run(
            [
                "pwsh", "-NoProfile", "-File", str(bundle / "hooks" / "approve-artifact.ps1"),
                "-ArtifactPath", ".ai-docs/instruction/new.md",
                "-ContentSha256", hashlib.sha256(new_payload["tool_input"]["command"].encode("utf-8")).hexdigest(),
                "-Approve",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if approval.returncode != 0:
            raise AssertionError(f"one-shot approval marker could not be created: {approval.stderr}")
        if invoke_codex(new_payload) != ("completed", ""):
            raise AssertionError("exact approved patch was not allowed")
        if invoke_codex(new_payload)[0] != "blocked":
            raise AssertionError("approval marker replay was not denied")

        claude_adapter_path = project / ".claude" / "hooks" / "claude-pre-tool-use.ps1"
        claude_denied = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(claude_adapter_path)],
            input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": "../claude-escape.md", "content": "blocked"}}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
            check=False,
        )
        if claude_denied.returncode != 2 or "Claude routing guard denied" not in claude_denied.stderr:
            raise AssertionError(f"Claude traversal write was not denied with exit 2: {claude_denied.stdout} / {claude_denied.stderr}")
        claude_allowed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(claude_adapter_path)],
            input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": ".ai-docs/instruction/existing.md", "content": "revised again"}}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
            check=False,
        )
        # Claude validates exit 0 JSON; the internal routing object failed as legacy decision=allow.
        if claude_allowed.returncode != 0 or claude_allowed.stdout.strip():
            raise AssertionError(f"Claude existing canonical edit was not allowed: {claude_allowed.stdout} / {claude_allowed.stderr}")
        # Claude settings run the shared core through Windows PowerShell 5.1.
        for (claude_payload, expected_code, expected_text), shell in [
            (case, shell)
            for case in (
                ({"tool_name": "Edit", "tool_input": {"file_path": ".ai-docs/instruction/existing.md", "new_string": "revised"}}, 0, ""),
                ({"tool_name": "Write", "tool_input": {"file_path": ".ai-docs/instruction/claude-new.md", "content": "new"}}, 2, "Claude routing guard denied: new managed artifact requires an exact one-shot approval marker"),
            )
            for shell in HOOK_SHELLS
        ]:
            claude_windows = subprocess.run(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(claude_adapter_path)],
                input=json.dumps(claude_payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
                check=False,
            )
            if claude_windows.returncode != expected_code or claude_windows.stdout.strip() or expected_text not in claude_windows.stderr:
                raise AssertionError(f"Claude {shell} regression: {claude_windows.stdout} / {claude_windows.stderr}")
        after_first_apply = snapshot_files(project)
        applied_again = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Apply", "-ApproveHostInstall"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if applied_again.returncode != 0 or snapshot_files(project) != after_first_apply:
            raise AssertionError("second approved Apply must be idempotent")
        checked = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if checked.returncode != 0 or {item["state"] for item in json.loads(checked.stdout)["hosts"]} != {"pending-trust"}:
            raise AssertionError("Check did not report both installed hosts as pending-trust")
        before_trust = snapshot_files(project)
        trust_denied = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-ActivateTrust", "-TargetHost", "codex"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if trust_denied.returncode == 0 or snapshot_files(project) != before_trust:
            raise AssertionError("unapproved Codex trust activation must fail without writing")
        trust_activated = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-ActivateTrust", "-TargetHost", "codex", "-ApproveTrustEvidence"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if trust_activated.returncode != 0:
            raise AssertionError(f"approved Codex trust activation failed: {trust_activated.stderr}")
        post_trust = json.loads(local_state_path.read_text(encoding="utf-8"))
        if post_trust["hosts"]["codex"]["status"] != "active" or post_trust["hosts"]["claude"]["status"] != "pending-trust":
            raise AssertionError("Codex trust activation did not preserve the other host's pending-trust state")

        def run_installer(*args: str) -> dict:
            completed = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            if completed.returncode != 0:
                raise AssertionError(f"install-routing {args} failed: {completed.stderr}")
            return {item["host"]: item for item in json.loads(completed.stdout)["hosts"]}

        codex_config_path = project / ".codex" / "hooks.json"
        if run_installer("-Apply", "-TargetHost", "codex", "-ApproveHostInstall")["codex"]["state"] != "active":
            raise AssertionError("re-Apply with an unchanged trusted Codex hook definition must keep active")
        codex_config = json.loads(read(codex_config_path))
        managed_entry = codex_config["hooks"]["PreToolUse"][0]
        legacy_entry = json.loads(json.dumps(managed_entry))
        legacy_entry["matcher"] = "apply_patch|Edit|Write|Bash|MCP|.*"
        legacy_posix_path = "/".join(("C:", "Users", "old-user", "old-project", ".codex", "hooks", "codex-pre-tool-use.ps1"))
        legacy_windows_path = "\\".join(("C:", "Users", "old-user", "old-project", ".codex", "hooks", "codex-pre-tool-use.ps1"))
        legacy_entry["hooks"][0]["command"] = f'pwsh -File "{legacy_posix_path}"'
        legacy_entry["hooks"][0]["commandWindows"] = f'powershell.exe -File "{legacy_windows_path}"'
        unrelated_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo unrelated"}]}
        codex_config["hooks"]["PreToolUse"] = [unrelated_entry, legacy_entry]
        codex_config_path.write_text(json.dumps(codex_config, indent=2), encoding="utf-8")
        if run_installer("-Check", "-TargetHost", "codex")["codex"]["state"] != "pending-trust":
            raise AssertionError("Check must not report active after the trusted Codex hook definition changed")
        if run_installer("-Apply", "-TargetHost", "codex", "-ApproveHostInstall")["codex"]["state"] != "pending-trust":
            raise AssertionError("re-Apply of a changed Codex hook definition must require trust again")
        migrated = json.loads(read(codex_config_path))["hooks"]["PreToolUse"]
        managed = [entry for entry in migrated if any("codex-pre-tool-use.ps1" in hook.get("commandWindows", "") for hook in entry["hooks"])]
        if len(managed) != 1 or managed[0]["matcher"] != "apply_patch|Bash" or unrelated_entry not in migrated:
            raise AssertionError(f"Codex re-Apply must replace the legacy managed entry once and keep unrelated hooks: {migrated}")
        installed_adapter = (project / ".codex" / "hooks" / "codex-pre-tool-use.ps1").read_bytes()
        if installed_adapter != (bundle / "hooks" / "codex-pre-tool-use.ps1").read_bytes():
            raise AssertionError("installed Codex adapter differs from the project-owned bundle source")
        routed = json.loads(read(bundle / "artifact-routing.json"))
        if any(key in routed["hosts"]["codex"] for key in ("status", "trust", "config_sha256")):
            raise AssertionError("shared routing contract regained host-local state")

        text_input = project / "external.mdx"
        text_input.write_text("<!-- harness-kit:managed:start -->\nNEW-MANAGED\n<!-- harness-kit:managed:end -->\n", encoding="utf-8", newline="\n")
        text_target = project / ".ai-docs" / "instruction" / "external.mdx"
        text_target.write_text("<!-- harness-kit:managed:start -->\nOLD-MANAGED\n<!-- harness-kit:managed:end -->\nUSER-EXTENSION\n", encoding="utf-8", newline="\n")
        normalizer_path = bundle / "normalize-artifact.ps1"
        normalizer_plan = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(normalizer_path), "-InputPath", str(text_input), "-ArtifactBundleId", "external-text", "-TargetPath", ".ai-docs/instruction/external.mdx", "-Plan"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if normalizer_plan.returncode != 0 or json.loads(normalizer_plan.stdout).get("status") != "proposal-only":
            raise AssertionError(f"text normalization plan failed: {normalizer_plan.stdout} / {normalizer_plan.stderr}")
        normalizer_denied = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(normalizer_path), "-InputPath", str(text_input), "-ArtifactBundleId", "external-text", "-TargetPath", ".ai-docs/instruction/external.mdx", "-Promote"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if normalizer_denied.returncode == 0:
            raise AssertionError("text promotion without G12 approval must fail")
        normalizer_promoted = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(normalizer_path), "-InputPath", str(text_input), "-ArtifactBundleId", "external-text", "-TargetPath", ".ai-docs/instruction/external.mdx", "-Promote", "-ApprovePromotion"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if normalizer_promoted.returncode != 0 or "NEW-MANAGED" not in read(text_target) or "USER-EXTENSION" not in read(text_target):
            raise AssertionError(f"approved marker-aware text promotion failed: {normalizer_promoted.stdout} / {normalizer_promoted.stderr}")
        fixed_input = project / "external.pdf"
        fixed_input.write_bytes(b"not-a-real-pdf")
        fixed_target = project / ".ai-docs" / "instruction" / "external.pdf"
        fixed_promoted = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(normalizer_path), "-InputPath", str(fixed_input), "-ArtifactBundleId", "external-fixed", "-TargetPath", ".ai-docs/instruction/external.pdf", "-Promote", "-ApprovePromotion"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        fixed_manifest = project / ".ai-docs" / "_inbox" / "external-fixed" / "artifact-manifest.json"
        if fixed_promoted.returncode != 0 or fixed_target.exists() or json.loads(read(fixed_manifest)).get("status") != "fixed-format-inbox-only":
            raise AssertionError(f"fixed-format promotion must remain inbox-only: {fixed_promoted.stdout} / {fixed_promoted.stderr}")
        fixed_metadata = json.loads(read(fixed_manifest))
        if fixed_metadata.get("source_name") != "external.pdf" or "source_path" in fixed_metadata:
            raise AssertionError("fixed-format manifest leaked its source PC path")
        removed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(bundle / "install-routing.ps1"), "-Uninstall", "-ApproveHostInstall"],
            capture_output=True,
            text=True,
            check=False,
        )
        if removed.returncode != 0 or (project / ".claude" / "hooks" / "claude-pre-tool-use.ps1").exists() or (project / ".codex" / "hooks" / "codex-pre-tool-use.ps1").exists():
            raise AssertionError("approved Uninstall did not remove only both host adapters")
        if "Read" not in read(claude_settings):
            raise AssertionError("Uninstall did not preserve unrelated Claude settings")


def check_portable_routing_lifecycle_contract() -> None:
    """B2 contract: lifecycle operations stay host-scoped and approval-gated."""
    installer = read(PORTABLE_ROUTING_TEMPLATE_ROOT / "install-routing.ps1.template")
    for token in ("-Plan", "-Apply", "-Check", "-Uninstall", "-ActivateTrust", "[ValidateSet('claude', 'codex', 'all')]", "ApproveHostInstall", "ApproveTrustEvidence"):
        if token not in installer:
            raise AssertionError(f"portable installer lifecycle operation missing: {token}")
    for template_name in ("root-context-single.template", "root-context.template"):
        template = read(SETUP_ROOT / "templates" / template_name)
        if ".ai-docs/harness/artifact-routing.json" not in template:
            raise AssertionError(f"{template_name}: missing portable routing Layer 1 summary")


def check_routing_coverage_manifest() -> None:
    """B5: keep every portable-routing requirement traceable to executable evidence."""
    coverage = json.loads(read(ROUTING_COVERAGE_MANIFEST))
    cases = coverage.get("cases", [])
    if [case.get("id") for case in cases] != list(range(1, 17)):
        raise AssertionError("routing coverage manifest must contain exactly ordered cases 1..16")
    for case in cases:
        for field in ("title", "fixture", "assertion", "evidence"):
            if not str(case.get(field, "")).strip():
                raise AssertionError(f"routing coverage case {case['id']} is missing {field}")
        for evidence_path in case["evidence"].split("; "):
            if not (REPO_ROOT / evidence_path).is_file():
                raise AssertionError(f"routing coverage evidence is missing: {evidence_path}")
    matrix = coverage.get("runtime_matrix", [])
    expected_matrix = {
        ("harness-kit-installed", "claude"),
        ("harness-kit-installed", "codex"),
        ("portable-only", "claude"),
        ("portable-only", "codex"),
    }
    if {(item.get("bundle"), item.get("host")) for item in matrix} != expected_matrix:
        raise AssertionError("routing coverage matrix must cover installed|portable-only × Claude|Codex")
    if any(item.get("expected_trust") not in {"pending-trust", "active"} for item in matrix):
        raise AssertionError("routing coverage matrix has invalid trust state")


def assert_allowed_outputs(project: Path, before: dict[str, str]) -> None:
    after = snapshot_files(project)
    changed = {
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    }
    invalid = sorted(
        path
        for path in changed
        if not (
            path == "AGENTS.md"
            or (path == "CLAUDE.md" and path in before and path not in after)
            or path.startswith(".ai-docs/")
        )
    )
    if invalid:
        raise AssertionError(f"fixture wrote outside setup allowlist: {invalid}")


def materialize_single_fixture(project: Path) -> None:
    docs = project / ".ai-docs"
    inbox = docs / "_inbox"
    inbox.mkdir(parents=True)
    (docs / "README.md").write_text(
        render("docs-readme-single.template"), encoding="utf-8", newline="\n"
    )
    (docs / ".gitignore").write_text(
        render("docs-gitignore.template"), encoding="utf-8", newline="\n"
    )
    (inbox / "README.md").write_text(
        render("inbox-readme.template"), encoding="utf-8", newline="\n"
    )
    (inbox / ".gitkeep").write_text("", encoding="utf-8")
    (project / "AGENTS.md").write_text(
        render(
            "root-context-single.template",
            {
                "{{APP_ID}}": "application",
            },
        ),
        encoding="utf-8",
        newline="\n",
    )


def materialize_multi_fixture(project: Path) -> None:
    docs = project / ".ai-docs"
    inbox = docs / "_inbox"
    root_context = docs / "root-context"
    inbox.mkdir(parents=True)
    root_context.mkdir(parents=True)
    for app in ("api", "web"):
        (docs / app / "context-base").mkdir(parents=True)
        (docs / app / "instruction").mkdir()
        (docs / app / "impl-doc").mkdir()
        (docs / f"{app}-context.md").write_text("", encoding="utf-8")
    (docs / "prototype").mkdir()
    (docs / "README.md").write_text(
        render("docs-readme-multi.template"), encoding="utf-8", newline="\n"
    )
    (docs / ".gitignore").write_text(
        render("docs-gitignore.template"), encoding="utf-8", newline="\n"
    )
    (inbox / "README.md").write_text(
        render("inbox-readme.template"), encoding="utf-8", newline="\n"
    )
    (inbox / ".gitkeep").write_text("", encoding="utf-8")
    agents = render(
        "root-context.template",
        {
            "{{APP_LIST}}": "| API | `api/` | fixture |\n| Web | `web/` | fixture |",
            "{{APP_CONTEXT_ENTRIES}}": "- `@.ai-docs/api-context.md`\n- `@.ai-docs/web-context.md`",
            "{{APP_INSTRUCTION_ENTRIES}}": "- `@.ai-docs/api/instruction/*-instruction.md`\n- `@.ai-docs/web/instruction/*-instruction.md`",
        },
    )
    for path, text in (
        (project / "AGENTS.md", agents),
        (root_context / "AGENTS.md", agents),
    ):
        path.write_text(text, encoding="utf-8", newline="\n")


def check_setup_contract() -> None:
    setup_files = [
        SETUP_ROOT / "SKILL.md",
        SETUP_ROOT / "prompts" / "single-app-setup.md",
        SETUP_ROOT / "prompts" / "multi-app-setup.md",
        SETUP_ROOT / "prompts" / "update-mode.md",
    ]
    setup_texts = {path: read(path) for path in setup_files}

    for path, text in setup_texts.items():
        for forbidden_path in FORBIDDEN_LOCAL_SKILL_PATHS:
            require(text, forbidden_path, path)

    skill_text = setup_texts[SETUP_ROOT / "SKILL.md"]
    require(skill_text, "허용되는 생성·갱신 범위는 `.ai-docs/**`, 루트 `AGENTS.md`뿐이다.", SETUP_ROOT / "SKILL.md")
    require(skill_text, "플러그인 리소스 해석 계약", SETUP_ROOT / "SKILL.md")
    require(skill_text, "`.ai-docs/`와 `AGENTS.md`가 모두 없음", SETUP_ROOT / "SKILL.md")
    require(skill_text, "`.ai-docs/` 또는 `AGENTS.md` 중 하나 이상 존재", SETUP_ROOT / "SKILL.md")
    for needle in (
        "artifact_fingerprint",
        "`.ai-docs/.harness/humanize-handoffs.json`",
        "`proposed`, `skipped`, `rejected`, `applied`, `revalidated`",
        "원자적 replace",
        "ledger는 Markdown이 아니며",
        "correlation 용도",
        "`harness-kit:managed:start/end` marker",
        "manual portable adoption",
        "G10",
        "`.codex/hooks.json`",
        "## 선택 권한 정책 연계",
        "`admin`은 앱 문서 권한을 상속하지 않는다",
        "`write_access_guard.py check-path`",
        "권한 정책을 자동 변경하거나 `project-write-access`를 자동 호출하지 않는다",
        "프로젝트 루트부터 파일시스템 루트까지",
        "Claude Code 2.1.277",
        "새 `CLAUDE.md`·`CLAUDE.local.md`는 생성하거나 갱신하지 않는다",
        "## 문서 루트 전환 계약",
        "`.docs/`만 있으면 **이전 문서 루트 이관 모드**",
        "`.docs/`와 `.ai-docs/`가 함께 있으면",
        "서명 권한 정책이 있으면 디렉토리를 옮기지 않는다",
        "`migrate-root-plan`과 `migrate-root`",
        "`harness-setup`이 이 권한 작업을 대신 실행하지 않는다",
        "사용자가 이번 요청에서 한국어 Markdown 문체 개선까지 명시한",
        "명시 요청이 없으면 이 절 전체를 건너뛰며",
    ):
        require(skill_text, needle, SETUP_ROOT / "SKILL.md")

    detection = read(SETUP_ROOT / "prompts" / "detection.md")
    require(detection, "이전 `.docs/`만 존재", SETUP_ROOT / "prompts" / "detection.md")
    require(detection, "`.ai-docs/`와 이전 `.docs/`가 함께 존재", SETUP_ROOT / "prompts" / "detection.md")
    require(detection, "위 조건 불충족", SETUP_ROOT / "prompts" / "detection.md")
    require(detection, "manual portable adoption", SETUP_ROOT / "prompts" / "detection.md")

    for path, needle in (
        (SETUP_ROOT / "prompts" / "single-app-setup.md", ".ai-docs/harness/"),
        (SETUP_ROOT / "prompts" / "multi-app-setup.md", ".ai-docs/harness/"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "current/proposed diff"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "G10"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "routing-state.local.json"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "byte-identical"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "migrated-{fingerprint}"),
        (SETUP_ROOT / "prompts" / "update-mode.md", "artifact-route-guard.ps1"),
    ):
        require(read(path), needle, path)

    update = setup_texts[SETUP_ROOT / "prompts" / "update-mode.md"]
    for needle in (
        "관리 블록만",
        "앞뒤 사용자 내용을 byte-preserve",
        "동시 수정",
        "`.ai-docs/archive/harness-setup/{timestamp}/{상대경로}`",
        "`unmanaged`",
        "`malformed`",
        "앱 핵심 문서를 `admin`이 대신 만들지 않는다",
        "`.ai-docs/harness/artifact-routing.json`의 앱·repository 지도",
        "`.ai-docs/root-context/AGENTS.md`가 Git 관리 원본",
        "루트 실행본을 관리 원본에 자동 역반영하지 않는다",
    ):
        require(update, needle, SETUP_ROOT / "prompts" / "update-mode.md")
    if "덮어써도 안전" in update:
        raise AssertionError("update-mode still claims unconditional overwrite is safe")

    command_pattern = re.compile(
        r"^\s*(?:mkdir|cp|copy|touch|new-item|set-content|write-output)\b.*"
        r"(?:\.agents[/\\]skills|\.claude[/\\]skills|(?:^|\s)skills[/\\])",
        re.IGNORECASE | re.MULTILINE,
    )
    for path, text in setup_texts.items():
        if command_pattern.search(text):
            raise AssertionError(f"{path}: command writes a local skill directory")

    combined = "\n".join(setup_texts.values())
    if 'cp "[plugin:harness-setup]' in combined:
        raise AssertionError("unresolvable plugin pseudo-path remains in a copy command")

    referenced_templates = set(
        re.findall(r"`templates/([A-Za-z0-9_.-]+)`", combined)
    )
    for template_name in referenced_templates:
        template_path = SETUP_ROOT / "templates" / template_name
        if not template_path.is_file():
            raise AssertionError(f"missing bundled template: {template_path}")

    single_template = read(SETUP_ROOT / "templates" / "root-context-single.template")
    if "{{PROJECT_NAME}}" in single_template or "{{PROJECT_ROOT}}" in single_template:
        raise AssertionError("single-app root map still embeds checkout identity")
    require(single_template, "프로젝트 루트: `./`", SETUP_ROOT / "templates" / "root-context-single.template")
    require(single_template, "{{APP_ID}}-context.md", SETUP_ROOT / "templates" / "root-context-single.template")
    if "context-doc`으로 보강" in single_template:
        raise AssertionError("single-app root map still delegates admin-owned root content to context-doc")
    for needle in ("다른 설치 스킬·플러그인·일반", "`플러그인 스킬만 사용` 규칙으로 확대하지 않는다"):
        require(needle=needle, text=single_template, source=SETUP_ROOT / "templates" / "root-context-single.template")

    multi_template = read(SETUP_ROOT / "templates" / "root-context.template")
    if "{{PROJECT_NAME}}" in multi_template or "{{PROJECT_ROOT}}" in multi_template:
        raise AssertionError("multi-app root map still embeds checkout identity")
    if "HARNESS_REPO_NAME" in multi_template:
        raise AssertionError("removed clone-era HARNESS_REPO_NAME remains")
    require(multi_template, ".ai-docs/root-context/AGENTS.md`가 Git 관리 원본", SETUP_ROOT / "templates" / "root-context.template")
    if "원본 복사본" in multi_template:
        raise AssertionError("multi-app root template still calls the management source a copy")
    for needle in ("산출물 유형", "`플러그인 스킬만 사용` 규칙으로 확대하지 않는다"):
        require(multi_template, needle, SETUP_ROOT / "templates" / "root-context.template")

    for template_name in ("docs-readme-single.template", "docs-readme-multi.template"):
        template_path = SETUP_ROOT / "templates" / template_name
        template = read(template_path)
        for needle in (
            "산출물 종류와 정규 위치",
            "제공 스킬 예시",
            "독점 실행 목록이 아니다",
            "다른 설치 스킬·플러그인·일반 Agent",
            "`플러그인 스킬만 사용` 또는 다른 도구 사용 금지로 해석하지 않는다",
        ):
            require(template, needle, template_path)
        if "정해진 스킬이 정해진 위치" in template:
            raise AssertionError(f"{template_path}: still binds an artifact path to one skill")

    for template_name in (
        "docs-readme-single.template",
        "docs-readme-multi.template",
        "root-context-single.template",
        "root-context.template",
    ):
        template = read(SETUP_ROOT / "templates" / template_name)
        for marker in MARKDOWN_MARKERS:
            require(template, marker, SETUP_ROOT / "templates" / template_name)
    for template_name in ("root-context-single.template", "root-context.template"):
        template_path = SETUP_ROOT / "templates" / template_name
        template = read(template_path)
        for needle in (
            "Claude Code 2.1.277",
            ".ai-docs/harness/artifact-routing.json",
            "pending-trust",
            "host별 승인과 신뢰",
        ):
            require(template, needle, template_path)
    if (SETUP_ROOT / "templates" / "claude-bridge.template").exists():
        raise AssertionError("retired CLAUDE.md bridge template still exists")
    gitignore = read(SETUP_ROOT / "templates" / "docs-gitignore.template")
    for marker in GITIGNORE_MARKERS:
        require(gitignore, marker, SETUP_ROOT / "templates" / "docs-gitignore.template")
    require(gitignore, "/.harness/routing-state.local.json", SETUP_ROOT / "templates" / "docs-gitignore.template")

    for path in SKILLS_ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".py", ".template"}:
            text = read(path).casefold()
            if "ke" + "ai" in text or "lhb" + "93" in text:
                raise AssertionError(f"user payload contains a case-specific project or user token: {path}")


def check_nested_handoff_contract() -> None:
    bootstrap = read(BOOTSTRAP_SKILL)
    context = read(CONTEXT_SKILL)

    for stale_path in ("../design-doc/", "../context-doc/"):
        if stale_path in bootstrap:
            raise AssertionError(f"{BOOTSTRAP_SKILL}: private cross-skill path remains")

    for needle in (
        "artifact_bundle_id",
        "handoff_owner = harness-bootstrap",
        "suppress_child_handoff = true",
        "공개 스킬 이름 `design-doc`",
        "공개 스킬 이름 `context-doc`",
        "handoff_completed = true",
    ):
        require(bootstrap, needle, BOOTSTRAP_SKILL)

    for needle in (
        "artifact_bundle_id",
        "handoff_owner = context-doc",
        "suppress_child_handoff = true",
        "handoff_completed = true",
    ):
        require(context, needle, CONTEXT_SKILL)

    for needle in (
        "공개 스킬 이름 `harness-setup`",
        "handoff_owner = harness-bootstrap",
        "suppress_child_handoff = true",
        "같은 질문을 반복하지 않는다",
        "`.agents/skills/`",
        "`.claude/skills/`",
        "`skills/`는 생성·복사·동기화하지 않는다",
    ):
        require(bootstrap, needle, BOOTSTRAP_SKILL)


def check_portable_routing_baseline() -> None:
    """Keep the read-only B0 evidence and the canonical inventory in sync."""
    baseline = json.loads(PORTABLE_BASELINE_FIXTURE.read_text(encoding="utf-8"))
    if baseline.get("schema_version") != "1.0.0":
        raise AssertionError("portable routing baseline schema version changed")
    case = baseline.get("case_study", {})
    if case.get("inspection_mode") != "read-only" or case.get("external_project_mutations") != 0:
        raise AssertionError("portable routing baseline must not claim external project writes")
    topology = case.get("topology", {})
    if topology.get("root_is_git") is not False or topology.get("docs_git") is not True:
        raise AssertionError("portable routing baseline lost root-non-git/docs-git topology")
    apps = topology.get("applications", [])
    if sorted((item.get("path"), item.get("is_git")) for item in apps) != [
        ("sample-api", True),
        ("sample-web", True),
    ]:
        raise AssertionError("portable routing baseline lost separate application Git topology")
    claude = baseline.get("host_capabilities", {}).get("claude", {})
    codex = baseline.get("host_capabilities", {}).get("codex", {})
    if claude.get("status") != "supported" or codex.get("status") != "supported":
        raise AssertionError("both host hook capabilities must be explicitly supported")
    if claude.get("project_scope") != ".claude/settings.json":
        raise AssertionError("Claude baseline must use project settings scope")
    if codex.get("project_scope") != ".codex/hooks.json" or "/hooks" not in codex.get("trust", ""):
        raise AssertionError("Codex baseline must record project hook path and hash trust")
    expected_failures = {"path-unbound-marker", "sibling-prefix-containment", "hardcoded-app-regex"}
    actual_failures = {item.get("id") for item in baseline.get("failure_fixtures", []) if item.get("expected") == "fail"}
    if actual_failures != expected_failures:
        raise AssertionError(f"portable routing failure fixtures drifted: {actual_failures}")
    decisions = {item.get("asset"): item.get("decision") for item in baseline.get("reuse_scan", [])}
    if decisions.get("maintainer/inventory/artifact-output-contract.json") != "extend":
        raise AssertionError("artifact output contract must be an extension target")
    if decisions.get("project-owned Codex hook adapter") != "new":
        raise AssertionError("Codex adapter must remain a later new asset")
    contract = json.loads((REPO_ROOT / "maintainer" / "inventory" / "artifact-output-contract.json").read_text(encoding="utf-8"))
    inventory = contract.get("portable_routing_baseline", {})
    if inventory.get("case_study_fixture") != "skills/harness-setup/evals/fixtures/portable-routing-baseline.json":
        raise AssertionError("artifact output contract does not point to the B0 baseline fixture")
    if {name: entry.get("status") for name, entry in inventory.get("host_capabilities", {}).items()} != {"claude": "supported", "codex": "supported"}:
        raise AssertionError("artifact output contract host capability baseline drifted")


def check_filesystem_fixtures() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-setup-fixtures-") as tmp:
        root = Path(tmp)

        single = root / "single-project"
        single.mkdir()
        before = snapshot_files(single)
        if detect_mode(single) != "initial":
            raise AssertionError("empty project must be initial mode")
        materialize_single_fixture(single)
        assert_allowed_outputs(single, before)
        artifacts = [
            single / ".ai-docs" / "README.md",
            single / "AGENTS.md",
        ]
        fingerprint, artifact_manifest = artifact_fingerprint(single, artifacts)
        ledger = single / ".ai-docs" / ".harness" / "humanize-handoffs.json"
        append_ledger_event(ledger, fingerprint, artifact_manifest, "proposed")
        stored = json.loads(ledger.read_text(encoding="utf-8"))
        if stored["records"][0]["artifact_fingerprint"] != fingerprint:
            raise AssertionError("ledger did not persist the artifact fingerprint")
        if stored["records"][0]["events"][0]["status"] != "proposed":
            raise AssertionError("ledger did not persist the proposal event")
        same_fingerprint, _ = artifact_fingerprint(single, artifacts)
        if same_fingerprint != fingerprint:
            raise AssertionError("same artifacts must produce the same fingerprint")
        (single / "AGENTS.md").write_text(
            (single / "AGENTS.md").read_text(encoding="utf-8") + "\nNEW-CONTENT\n",
            encoding="utf-8",
            newline="\n",
        )
        changed_fingerprint, changed_manifest = artifact_fingerprint(single, artifacts)
        if changed_fingerprint == fingerprint:
            raise AssertionError("content changes must produce a new fingerprint")
        append_ledger_event(
            ledger,
            changed_fingerprint,
            changed_manifest,
            "applied",
            supersedes_fingerprint=fingerprint,
        )
        append_ledger_event(
            ledger,
            changed_fingerprint,
            changed_manifest,
            "revalidated",
        )
        updated_ledger = json.loads(ledger.read_text(encoding="utf-8"))
        final_record = next(
            record
            for record in updated_ledger["records"]
            if record["artifact_fingerprint"] == changed_fingerprint
        )
        if final_record.get("supersedes_fingerprint") != fingerprint:
            raise AssertionError("post-apply fingerprint did not link proposal fingerprint")
        if [event["status"] for event in final_record["events"]] != [
            "applied",
            "revalidated",
        ]:
            raise AssertionError("post-apply final fingerprint is not terminal")
        if ledger.with_name(f".{ledger.name}.tmp").exists():
            raise AssertionError("atomic ledger temporary file was not cleaned up")
        assert_allowed_outputs(single, before)
        if find_claude_instruction_files(single, root):
            raise AssertionError("fresh single-app setup created a Claude instruction file")
        for rel in FORBIDDEN_LOCAL_SKILL_PATHS:
            if (single / rel.rstrip("/")).exists():
                raise AssertionError(f"single fixture created forbidden path: {rel}")

        checkout_a = root / "renamed-checkout-a"
        checkout_b = root / "renamed-checkout-b"
        materialize_single_fixture(checkout_a)
        materialize_single_fixture(checkout_b)
        if snapshot_files(checkout_a) != snapshot_files(checkout_b):
            raise AssertionError("shared setup output changes when only the checkout folder name changes")

        multi = root / "multi-project"
        (multi / "api").mkdir(parents=True)
        (multi / "web").mkdir()
        before = snapshot_files(multi)
        materialize_multi_fixture(multi)
        assert_allowed_outputs(multi, before)
        if find_claude_instruction_files(multi, root):
            raise AssertionError("fresh multi-app setup created a Claude instruction file")
        if (multi / ".ai-docs" / "root-context" / "CLAUDE.md").exists():
            raise AssertionError("fresh multi-app setup created a retired root-context bridge")
        for rel in FORBIDDEN_LOCAL_SKILL_PATHS:
            if (multi / rel.rstrip("/")).exists():
                raise AssertionError(f"multi fixture created forbidden path: {rel}")

        partial_docs = root / "partial-docs"
        (partial_docs / ".ai-docs").mkdir(parents=True)
        if detect_mode(partial_docs) != "update":
            raise AssertionError(".ai-docs-only project must use update/recovery mode")
        partial_agents = root / "partial-agents"
        partial_agents.mkdir()
        (partial_agents / "AGENTS.md").write_text("# Existing\n", encoding="utf-8")
        if detect_mode(partial_agents) != "update":
            raise AssertionError("AGENTS-only project must use update/recovery mode")

        ancestor = root / "ancestor-instruction"
        nested_project = ancestor / "project"
        nested_project.mkdir(parents=True)
        (ancestor / "CLAUDE.local.md").write_text("team override\n", encoding="utf-8")
        found = find_claude_instruction_files(nested_project, root)
        if found != [ancestor / "CLAUDE.local.md"]:
            raise AssertionError(f"ancestor Claude instruction preemption was not detected: {found}")

        user_bridge_project = root / "user-claude-project"
        user_bridge_project.mkdir()
        user_bridge = user_bridge_project / "CLAUDE.md"
        user_bridge.write_text(LEGACY_MANAGED_BRIDGE + "\nTEAM-CLAUDE-DELTA\n", encoding="utf-8")
        if is_managed_only_claude_bridge(user_bridge):
            raise AssertionError("user content outside the managed bridge was treated as removable")

        legacy_root = root / "legacy-document-root"
        (legacy_root / ".docs").mkdir(parents=True)
        if detect_mode(legacy_root) != "legacy-document-root-migration":
            raise AssertionError("legacy .docs project must use explicit document-root migration mode")
        conflicting_roots = root / "conflicting-document-roots"
        (conflicting_roots / ".docs").mkdir(parents=True)
        (conflicting_roots / ".ai-docs").mkdir()
        if detect_mode(conflicting_roots) != "document-root-conflict":
            raise AssertionError("coexisting document roots must stop as a conflict")

        update = root / "update-project"
        materialize_single_fixture(update)
        custom_tokens = {
            update / ".ai-docs" / "README.md": "TEAM-README-EXTENSION",
            update / ".ai-docs" / ".gitignore": "team-private.cache",
            update / "AGENTS.md": "TEAM-AGENT-RULE",
        }
        for path, token in custom_tokens.items():
            path.write_text(
                path.read_text(encoding="utf-8") + f"\n{token}\n",
                encoding="utf-8",
                newline="\n",
            )

        legacy_files = [
            update / ".agents" / "skills" / "custom-skill" / "SKILL.md",
            update / ".claude" / "skills" / "legacy-skill" / "SKILL.md",
            update / "skills" / "domain-skill" / "SKILL.md",
        ]
        for path in legacy_files:
            path.parent.mkdir(parents=True)
            path.write_text("legacy sentinel\n", encoding="utf-8")
        legacy_before = {path: sha256(path) for path in legacy_files}
        before = snapshot_files(update)

        replacement_templates = {
            update / ".ai-docs" / "README.md": (
                render("docs-readme-single.template"),
                MARKDOWN_MARKERS,
            ),
            update / ".ai-docs" / ".gitignore": (
                render("docs-gitignore.template"),
                GITIGNORE_MARKERS,
            ),
            update / "AGENTS.md": (
                render(
                    "root-context-single.template",
                    {
                        "{{APP_ID}}": "application",
                    },
                ),
                MARKDOWN_MARKERS,
            ),
        }
        for path, (template, markers) in replacement_templates.items():
            current = path.read_text(encoding="utf-8")
            path.write_text(
                replace_managed_block(current, template, markers),
                encoding="utf-8",
                newline="\n",
            )
        assert_allowed_outputs(update, before)
        for path, token in custom_tokens.items():
            if token not in path.read_text(encoding="utf-8"):
                raise AssertionError(f"user extension was not preserved: {path}")
        for path, digest in legacy_before.items():
            if sha256(path) != digest:
                raise AssertionError(f"legacy local skill copy changed: {path}")

        managed_bridge = update / "CLAUDE.md"
        managed_bridge.write_text(LEGACY_MANAGED_BRIDGE, encoding="utf-8", newline="\n")
        if not is_managed_only_claude_bridge(managed_bridge):
            raise AssertionError("known harness-managed bridge was not recognized")
        before_bridge_removal = snapshot_files(update)
        managed_bridge.unlink()
        assert_allowed_outputs(update, before_bridge_removal)
        if find_claude_instruction_files(update, root):
            raise AssertionError("approved managed bridge migration left a Claude instruction file")


def main() -> int:
    check_setup_contract()
    check_nested_handoff_contract()
    check_portable_routing_baseline()
    check_portable_routing_bundle()
    check_portable_routing_lifecycle_contract()
    check_routing_coverage_manifest()
    check_filesystem_fixtures()
    print("harness setup contract and filesystem fixture evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

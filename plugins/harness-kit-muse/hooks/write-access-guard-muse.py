#!/usr/bin/env python3
"""Muse PreToolUse adapter for the project write-access guard.

Muse invokes a hook as a fixed ``command`` with the event payload on stdin
and no per-project arguments, while ``write_access_guard.py`` requires an
``ai`` subcommand with ``--host`` and ``--project-root``. This adapter
bridges the two: it derives the project root from the payload ``cwd``
(falling back to the hook runner cwd) and forwards the original stdin bytes
to the guard unchanged. Guard stdout, stderr, and exit code pass through so
the hook host observes the guard decision directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "write-access-guard.py"


def derive_root(payload: dict) -> str:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd and Path(cwd).is_dir():
        return cwd
    return os.getcwd()


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    root = derive_root(payload)
    proc = subprocess.run(
        [sys.executable, str(GUARD), "ai", "--host", "codex", "--project-root", root],
        input=raw,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())

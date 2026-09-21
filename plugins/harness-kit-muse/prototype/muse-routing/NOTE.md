# Muse routing prototype — findings (Phase B)

Verified against `muse` CLI with a live PreToolUse probe. Official docs:
`dev.meta.ai/docs/muse-code/extending` (#skills, #hooks).

## Live hook payload (stdin JSON)

- `hook_event_name`: `"PreToolUse"`
- `tool_name`: Muse tool id, e.g. `write_file` (differs from Codex/Claude names)
- `tool_input`: tool params, e.g. `{"path": ..., "content": ...}` for `write_file`
- `cwd`: project root. `argv`: empty. Extra keys: `tool_use_id`,
  `session_id`, `turn_id`, `transcript_path`, `model`, `permission_mode`,
  `model_provider`.
- Environment is a cleared allowlist (observed: `COMSPEC`, `LANG`, `PATH`,
  `PATHEXT`, `PROMPT`, `PYTHONUSERBASE`, `SYSTEMROOT`, `TEMP`, `TERM`, `TMP`,
  `WINDIR`).

## Project hooks config

`<project>/.muse/hooks.json` with the Claude-shaped
`{"hooks": {"PreToolUse": [{"matcher": ..., "hooks": [{"type": "command",
"command": ...}]}}]}` fires on every matching tool call after project trust.
Malformed files are skipped with a startup warning.

## Decision mapping (observed)

- exit 0 + empty stdout → allow.
- exit 0 + `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": ...}}` →
  `permission_decision: deny`, effects `blocked` + `permission_denied`.
- nonzero exit (e.g. argparse usage error) → `should_block: true` with
  `permission_decision: null`. A broken hook command therefore blocks
  everything: never approve an untested hook.

## Prototype rule (muse-route.py)

Denies `*.md` writes under a `protected/` path part, allows everything else,
fail-open (silent allow) on malformed input. Verified 7/7 direct matrix
(relative/absolute/namespaced-tool/non-write/malformed/similar-name/BOM)
plus live allow (`hello2.txt` created) and live deny (`protected/note.md`
absent).

## Open items for source integration

- Full routing table (read `artifact-routing.json` + instruction) instead of
  the single prototype rule.
- `harness-setup` template set for the Muse target (source change: rebuild +
  revalidate all runtimes + docs).
- Guard allow-path needs an enrolled project (policy + git-scoped-account).

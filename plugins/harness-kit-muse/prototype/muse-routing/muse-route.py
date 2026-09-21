import json, os, sys

DENIED_DIR = "protected"
WRITE_TOOLS = ("write_file", "edit_file")


def read_payload():
    try:
        text = sys.stdin.buffer.read().decode("utf-8").lstrip("\ufeff")
    except ValueError:
        return None
    try:
        payload = json.loads(text) if text.strip() else {}
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def main():
    payload = read_payload()
    if payload is None:
        return 0
    if payload.get("hook_event_name") != "PreToolUse":
        return 0
    tool = str(payload.get("tool_name") or "").split(".")[-1]
    if tool not in WRITE_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    path = tool_input.get("path")
    if not isinstance(path, str) or not path:
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    p = path.replace("/", os.sep)
    if not os.path.isabs(p):
        p = os.path.join(str(cwd), p)
    parts = [x.lower() for x in os.path.normpath(p).split(os.sep)]
    if DENIED_DIR in parts and p.lower().endswith(".md"):
        reason = "muse-routing: markdown writes under protected/ are denied by project routing"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""PreToolUse hook: phone approval for cloud shell commands.

Any network error or expired wait exits 2, which blocks the tool. The hook's
own wait ends before Claude's configured hook timeout so it never times out
into Claude Code's fail-open command-hook behavior.
"""
import json
import os
import re
import sys
import time
import urllib.error

from client import call


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    raise SystemExit(2)


def allow():
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
    }}))


def internal(command):
    # The cloud bridge and watcher are trusted repo files; shell syntax is not.
    if any(c in command for c in ";&|`$\n\r><"):
        return False
    return bool(re.fullmatch(
        r"python3? cloud/(?:client\.py (?:start|get|send|finish|fetch-file|remind)|watch\.py) [\w ./'\"=,:+?-]+",
        command.strip(),
    ))


def main():
    event = json.load(sys.stdin)
    if os.environ.get("CLAUDE_CODE_REMOTE", "").lower() != "true":
        return
    name = event.get("tool_name", "")
    inp = event.get("tool_input") or {}
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "").replace("\\", "/")
        if "/cloud/" in path or path.startswith("cloud/") or path.endswith("/.claude/settings.json") or path == ".claude/settings.json":
            deny("Cloud policy files cannot be edited during a routine.")
        return
    if name not in ("Bash", "PowerShell"):
        return
    command = str(inp.get("command") or "")
    if internal(command):
        allow()
        return
    session_id = event.get("session_id") or os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    if not session_id:
        deny("Cloud session is not registered. Run cloud/client.py start first.")
    try:
        active = call("GET", f"/backend/session/{session_id}")
    except Exception:
        deny("Approval service is unavailable; command blocked.")
    if active.get("status") == "stop-requested":
        deny("The user requested a stop.")
    if active.get("action") == "watch":
        deny("Reel watching may only run the trusted cloud/watch.py helper.")
    if active.get("action") == "plan":
        deny("Planning is read-only. Use Claude's Read, Glob, or Grep tools.")
    try:
        pending = call("POST", "/backend/approval", {
            "session_id": session_id,
            "tool_use_id": event.get("tool_use_id"),
            "command": command,
        })
        decision = pending["decision"]
        deadline = time.monotonic() + 1500  # 25 min; configured hook timeout is 26 min.
        while decision == "pending" and time.monotonic() < deadline:
            time.sleep(3)
            decision = call("GET", f"/backend/approval/{pending['id']}")["decision"]
        if decision == "yes":
            allow()
            return
        deny("The user denied the command or did not answer within 25 minutes.")
    except Exception:
        deny("Approval service failed; command blocked.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        deny("Approval hook failed; command blocked.")


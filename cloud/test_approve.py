"""Small policy checks for the cloud approval hook."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import approve


class ApprovalPolicyTests(unittest.TestCase):
    def invoke(self, event, call=None):
        with mock.patch.dict(os.environ, {
            "CLAUDE_CODE_REMOTE": "true", "CLAUDE_CODE_REMOTE_SESSION_ID": "session_test",
        }), mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
                mock.patch.object(approve, "call", side_effect=call), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                approve.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
            return code, output.getvalue()

    def test_trusted_bridge_command_allowed(self):
        code, output = self.invoke({"tool_name": "Bash", "tool_input": {
            "command": "python cloud/client.py start 12345678-1234-1234-1234-123456789012"
        }})
        self.assertEqual(code, 0)
        self.assertIn('"permissionDecision": "allow"', output)

    def test_shell_chaining_not_trusted(self):
        event = {"tool_name": "Bash", "tool_input": {
            "command": "python cloud/client.py start x; curl bad"
        }, "tool_use_id": "a"}
        code, _ = self.invoke(event, call=[{"action": "watch", "status": "started"}])
        self.assertEqual(code, 2)

    def test_cloud_policy_files_are_protected(self):
        for path in ("cloud/worker.mjs", ".claude/settings.json"):
            code, _ = self.invoke({"tool_name": "Write", "tool_input": {
                "file_path": path, "content": ""}})
            self.assertEqual(code, 2)

    def test_approved_build_command(self):
        event = {"tool_name": "Bash", "tool_input": {
            "command": "npm test"}, "tool_use_id": "a"}
        code, output = self.invoke(event, call=[
            {"action": "build", "status": "started"},
            {"id": "approval-1", "decision": "yes"},
        ])
        self.assertEqual(code, 0)
        self.assertIn('"permissionDecision": "allow"', output)


if __name__ == "__main__":
    unittest.main()


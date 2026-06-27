import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maos_runtime.a2a import provider_capabilities
from maos_runtime.a2a.claude_backend import (
    _claude_command,
    _claude_completion_error,
    _claude_output,
    _display_command,
)


class ClaudeProviderTests(unittest.TestCase):
    def test_claude_command_uses_safe_noninteractive_defaults(self) -> None:
        command = _claude_command(
            {
                "claude_bin": "claude",
                "model": "glm-5.1",
                "permission_mode": "acceptEdits",
                "claude_args": ["--verbose"],
            },
            {"id": "node"},
        )

        self.assertTrue(command[0].lower().endswith(("claude", "claude.cmd", "claude.exe")))
        self.assertEqual(command[1], "--print")
        self.assertIn("--bare", command)
        self.assertIn("--model", command)
        self.assertIn("glm-5.1", command)
        self.assertIn("--permission-mode", command)
        self.assertIn("acceptEdits", command)
        self.assertIn("--output-format", command)
        self.assertIn("json", command)
        self.assertIn("--verbose", command)
        self.assertNotIn("Do the task.", command)

    def test_claude_command_defaults_to_bailian_glm_model(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            command = _claude_command({}, {"id": "node"})

        self.assertIn("--model", command)
        self.assertIn("glm-5.1", command)
        self.assertIn("--bare", command)

    def test_claude_command_can_disable_bare_and_configure_tools(self) -> None:
        command = _claude_command(
            {"tools": ["Read", "Grep"], "allowed_tools": ["Read", "Grep"]},
            {"id": "node", "bare": False},
        )

        self.assertNotIn("--bare", command)
        self.assertIn("--tools", command)
        self.assertIn("Read,Grep", command)
        self.assertIn("--allowedTools", command)

    def test_claude_command_allows_read_only_uri_fetch_by_default(self) -> None:
        command = _claude_command({}, {"id": "node"})

        self.assertIn("--tools", command)
        self.assertIn("Bash,WebFetch", command)
        self.assertIn("--allowedTools", command)
        self.assertIn("Bash(curl *),Bash(wget *),WebFetch", command)

    def test_claude_output_extracts_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.json"
            path.write_text(json.dumps({"result": "final answer"}), encoding="utf-8")

            self.assertEqual(_claude_output({"claudeStdoutFile": str(path)}), "final answer")

    def test_display_command_redacts_prompt(self) -> None:
        display = _display_command(["claude", "--print"])

        self.assertIn("<stdin>", display)
        self.assertNotIn("secret prompt", display)

    def test_claude_completion_error_detects_failed_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.json"
            path.write_text(
                json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": ""}),
                encoding="utf-8",
            )

            self.assertIsNotNone(_claude_completion_error({"claudeStdoutFile": str(path)}))

    def test_claude_provider_capabilities_are_registered(self) -> None:
        capabilities = provider_capabilities()

        self.assertIn("claude", capabilities["providers"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["cancel"])


if __name__ == "__main__":
    unittest.main()

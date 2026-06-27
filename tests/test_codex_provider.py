import unittest
from pathlib import Path

from maos_runtime.a2a.codex_backend import _codex_command


class CodexProviderTests(unittest.TestCase):
    def test_codex_command_uses_safe_noninteractive_defaults(self) -> None:
        command = _codex_command(
            {
                "codex_bin": "codex",
                "model": "gpt-5",
                "approval_policy": "never",
                "codex_args": ["--color", "never"],
            },
            {"id": "node"},
            Path(r"D:\dev\MAOS\temporal_execution_core"),
            Path(r"D:\dev\MAOS\temporal-data\codex-provider\test\final_output.md"),
        )

        self.assertEqual(command[1:4], ["-a", "never", "exec"])
        self.assertIn("--json", command)
        self.assertIn("--output-last-message", command)
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("--model", command)
        self.assertIn("gpt-5", command)
        self.assertEqual(command[-1], "-")


if __name__ == "__main__":
    unittest.main()

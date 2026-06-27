import json
import os
import tempfile
import unittest
from pathlib import Path

from web.web_agent_api import build_local_runtime_output


class LocalRuntimeOutputProjectionTests(unittest.TestCase):
    def test_reads_full_claude_result_file(self) -> None:
        long_output = "# 完整报告\n\n" + ("这一段不能被截断。" * 2000)
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-test" / "stdout.json"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    json.dumps({"type": "result", "result": long_output}, ensure_ascii=False),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-test", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["output_chars"], len(long_output))
        self.assertEqual(result["final_output"]["content"], long_output)
        self.assertNotIn("...<truncated>", result["final_output"]["content"])

    def test_reads_full_codex_final_output_file(self) -> None:
        long_output = "# Codex 完整报告\n\n" + ("完整内容。" * 2000)
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "codex-provider" / "a2a-task-test" / "final_output.md"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(long_output, encoding="utf-8")

                result = build_local_runtime_output("a2a-task-test", "codex")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["output_chars"], len(long_output))
        self.assertEqual(result["final_output"]["content"], long_output)
        self.assertNotIn("...<truncated>", result["final_output"]["content"])


if __name__ == "__main__":
    unittest.main()

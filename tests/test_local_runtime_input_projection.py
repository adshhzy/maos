import os
import tempfile
import unittest
from pathlib import Path

from web.web_agent_api import build_local_runtime_input


class LocalRuntimeInputProjectionTests(unittest.TestCase):
    def test_reads_full_claude_prompt_file(self) -> None:
        long_output = "FULL_UPSTREAM_OUTPUT " * 1000
        prompt = f"""# Local Runtime Task

## Node Instruction

Review upstream result.

## Original Business Input

```json
{{"task": "demo"}}
```

## Upstream Node Results

```json
{{"upstream": {{"payload": {{"summary": "{long_output}"}}}}}}
```
"""
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                prompt_file = Path(tmp) / "claude-provider" / "a2a-task-test" / "prompt.md"
                prompt_file.parent.mkdir(parents=True)
                prompt_file.write_text(prompt, encoding="utf-8")

                result = build_local_runtime_input("a2a-task-test", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["prompt_chars"], len(prompt))
        self.assertIn(long_output, result["agent_input"]["upstream_node_results"])
        self.assertNotIn("...<truncated>", result["agent_input"]["upstream_node_results"])


if __name__ == "__main__":
    unittest.main()

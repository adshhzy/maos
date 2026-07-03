import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maos_runtime.a2a import provider_capabilities
from maos_runtime.a2a_constants import CLAUDE_HUAWEI_BACKEND
from maos_runtime.a2a.claude_backend import (
    _claude_command,
    _claude_completion_error,
    _claude_process_env,
    _claude_run_dir,
    _claude_trace,
    _claude_output,
    _display_command,
    _enforce_control_flow_decision_policy,
    _resolve_dependency_artifact_refs,
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
        self.assertIn("stream-json", command)
        self.assertIn("--include-partial-messages", command)
        self.assertIn("--include-hook-events", command)
        self.assertIn("--verbose", command)
        self.assertNotIn("Do the task.", command)

    def test_claude_command_defaults_to_bailian_glm_model(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            command = _claude_command({}, {"id": "node"})

        self.assertIn("--model", command)
        self.assertIn("glm-5.1", command)
        self.assertIn("--bare", command)
        self.assertIn("--output-format", command)
        self.assertIn("stream-json", command)
        self.assertIn("--verbose", command)

    def test_claude_huawei_command_defaults_to_deepseek_model(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            command = _claude_command({}, {"id": "node"}, backend=CLAUDE_HUAWEI_BACKEND)

        self.assertIn("--model", command)
        self.assertIn("deepseek-v3.2", command)
        self.assertIn("--bare", command)
        self.assertIn("--output-format", command)
        self.assertIn("stream-json", command)

    def test_claude_huawei_uses_distinct_runtime_directory(self) -> None:
        self.assertIn("claude-provider", str(_claude_run_dir("task-1")))
        self.assertIn("claude-huawei-provider", str(_claude_run_dir("task-1", CLAUDE_HUAWEI_BACKEND)))

    def test_claude_huawei_process_env_is_isolated_from_default_claude(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "aliyun-token",
                "CLAUDE_HUAWEI_ANTHROPIC_BASE_URL": "https://huawei.example/anthropic",
                "CLAUDE_HUAWEI_API_KEY": "huawei-token",
            },
            clear=True,
        ):
            env = _claude_process_env({}, {"id": "node"}, CLAUDE_HUAWEI_BACKEND)

        assert env is not None
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://huawei.example/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "huawei-token")
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], "x-api-key: huawei-token")
        self.assertEqual(env["CLAUDE_CLI_MODEL"], "deepseek-v3.2")
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].endswith("claude-huawei-provider-config"))

    def test_claude_huawei_requires_explicit_endpoint_and_token(self) -> None:
        with patch.dict(
            "os.environ",
            {"MAOS_DISABLE_WINDOWS_ENV_FALLBACK": "1"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                _claude_process_env({}, {"id": "node"}, CLAUDE_HUAWEI_BACKEND)

    def test_claude_command_can_disable_bare_and_configure_tools(self) -> None:
        command = _claude_command(
            {"tools": ["Read", "Grep"], "allowed_tools": ["Read", "Grep"]},
            {"id": "node", "bare": False},
        )

        self.assertNotIn("--bare", command)
        self.assertIn("--tools", command)
        self.assertIn("Read,Grep", command)
        self.assertIn("--allowedTools", command)

    def test_claude_command_can_disable_all_runtime_tools(self) -> None:
        command = _claude_command(
            {
                "disable_tools": True,
                "tools": ["Bash", "WebFetch"],
                "allowed_tools": ["Bash(curl *)", "WebFetch"],
            },
            {"id": "node"},
        )

        self.assertNotIn("--tools", command)
        self.assertNotIn("--allowedTools", command)
        self.assertTrue(_resolve_dependency_artifact_refs({"disable_tools": True}, {"id": "node"}))

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

    def test_claude_output_extracts_stream_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "system", "session_id": "s1"}),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "content": [
                                        {"type": "tool_use", "name": "Bash", "input": {"command": "echo ok"}},
                                        {"type": "text", "text": "working"},
                                    ]
                                },
                            }
                        ),
                        json.dumps({"type": "result", "subtype": "success", "result": "final stream answer"}),
                    ]
                ),
                encoding="utf-8",
            )

            metadata = {"claudeStdoutFile": str(path), "claudeOutputFormat": "stream-json"}
            self.assertEqual(_claude_output(metadata), "final stream answer")
            self.assertIsNone(_claude_completion_error(metadata))

    def test_claude_trace_merges_streaming_text_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "system", "session_id": "s1"}),
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}),
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": " world"}]}}),
                        json.dumps({"type": "result", "subtype": "success", "result": "done"}),
                    ]
                ),
                encoding="utf-8",
            )

            trace = _claude_trace(
                {
                    "claudeStdoutFile": str(path),
                    "claudeOutputFormat": "stream-json",
                    "startedAt": 1,
                },
                2.0,
            )

        text_steps = [step for step in trace if step.get("type") == "claude.text"]
        self.assertEqual(len(text_steps), 1)
        self.assertEqual(text_steps[0]["preview"], "hello world")
        self.assertEqual(text_steps[0]["chunk_count"], 2)

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
        self.assertIn("claude-huawei", capabilities["providers"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["claude"]["operations"]["cancel"])
        self.assertTrue(capabilities["providers"]["claude-huawei"]["operations"]["create"])

    def test_early_risk_pass_is_routed_as_revision_when_configured(self) -> None:
        adjusted = _enforce_control_flow_decision_policy(
            {
                "decision": "approved_with_risk",
                "reason": "tests are broken",
                "required_changes": ["fix tests"],
                "risks": ["test suite cannot run"],
            },
            {
                "riskPassRequiresFinalVisit": True,
                "controlFlowVisit": 1,
                "controlFlowMaxVisits": 3,
            },
        )

        self.assertEqual(adjusted["decision"], "needs_revision")
        self.assertEqual(adjusted["original_decision"], "approved_with_risk")

    def test_final_visit_risk_pass_is_preserved_when_configured(self) -> None:
        adjusted = _enforce_control_flow_decision_policy(
            {
                "decision": "approved_with_risk",
                "reason": "last pass with residual risk",
                "required_changes": ["document residual risk"],
                "risks": ["non-blocking limitation"],
            },
            {
                "riskPassRequiresFinalVisit": True,
                "controlFlowVisit": 3,
                "controlFlowMaxVisits": 3,
            },
        )

        self.assertEqual(adjusted["decision"], "approved_with_risk")
        self.assertNotIn("original_decision", adjusted)


if __name__ == "__main__":
    unittest.main()

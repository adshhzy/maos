import unittest

from maos_runtime.a2a.prompt_builders import (
    _hermes_dependency_context,
    _hermes_expected_output_hint,
    _hermes_prompt,
    _multica_description,
)


class HermesPromptBuilderTests(unittest.TestCase):
    def test_markdown_report_prompt_is_not_inferred_as_json(self) -> None:
        hint = _hermes_expected_output_hint(
            "Please generate a structured Markdown report. Do not output JSON."
        )

        self.assertEqual(hint["format"], "structured_chinese_text")

    def test_explicit_json_prompt_is_inferred_as_json(self) -> None:
        hint = _hermes_expected_output_hint(
            "Return JSON object with fields: decision, reason."
        )

        self.assertEqual(hint["format"], "json_object")

    def test_dependency_context_keeps_long_business_text(self) -> None:
        long_output = "A" * 4000

        context = _hermes_dependency_context(
            {
                "upstream": {
                    "node": "upstream",
                    "operation": "agent_task",
                    "payload": {
                        "latest_comment": long_output,
                        "comments": [{"text": "large trace field"}],
                    },
                }
            }
        )

        payload = context["upstream"]["payload"]
        self.assertEqual(payload["summary"], long_output)
        self.assertNotIn("latest_comment", payload)
        self.assertNotIn("comments", payload)

    def test_hermes_prompt_omits_full_a2a_message_envelope(self) -> None:
        prompt = _hermes_prompt(
            {
                "id": "review_node",
                "label": "评审节点",
                "operation": "agent_task",
                "agent": {
                    "prompt": "请基于任务输入和上游节点结果输出中文评审结论。",
                },
            },
            {
                "draft_node": {
                    "node": "draft_node",
                    "operation": "agent_task",
                    "duration_seconds": 1.5,
                    "payload": {"latest_comment": "上游草案结果"},
                }
            },
            {"task": "准备上线评审"},
            workflow_id="workflow-test",
            a2a_task_id="a2a-task-test",
            context_policy="provided_context_only",
            runtime_profile="hermes_oneshot",
        )

        self.assertIn("## 任务指令", prompt)
        self.assertIn("## 原始业务输入", prompt)
        self.assertIn("## 上游节点结果", prompt)
        self.assertIn("准备上线评审", prompt)
        self.assertIn("上游草案结果", prompt)

        self.assertNotIn("## A2A 消息载荷", prompt)
        self.assertNotIn("parts[0]", prompt)
        self.assertNotIn("parts[1]", prompt)
        self.assertNotIn("messageId", prompt)

    def test_hermes_prompt_keeps_full_dependency_text_by_default(self) -> None:
        long_output = "full upstream business output " * 400

        prompt = _hermes_prompt(
            {
                "id": "review_node",
                "label": "review",
                "operation": "agent_task",
                "agent": {
                    "prompt": "Review upstream result.",
                    "prompt_payload_limit": 200,
                },
            },
            {
                "draft_node": {
                    "node": "draft_node",
                    "operation": "agent_task",
                    "payload": {"latest_comment": long_output},
                }
            },
            {"task": "demo"},
            workflow_id="workflow-test",
            a2a_task_id="a2a-task-test",
            context_policy="provided_context_only",
            runtime_profile="hermes_oneshot",
        )

        self.assertIn(long_output, prompt)
        self.assertNotIn("...<truncated>", prompt)

    def test_hermes_prompt_truncates_only_when_explicitly_enabled(self) -> None:
        long_output = "full upstream business output " * 400

        prompt = _hermes_prompt(
            {
                "id": "review_node",
                "label": "review",
                "operation": "agent_task",
                "agent": {
                    "prompt": "Review upstream result.",
                    "prompt_payload_limit": 200,
                    "truncate_prompt_payload": True,
                },
            },
            {
                "draft_node": {
                    "node": "draft_node",
                    "operation": "agent_task",
                    "payload": {"latest_comment": long_output},
                }
            },
            {"task": "demo"},
            workflow_id="workflow-test",
            a2a_task_id="a2a-task-test",
            context_policy="provided_context_only",
            runtime_profile="hermes_oneshot",
        )

        self.assertIn("...<truncated>", prompt)


class MulticaDescriptionBuilderTests(unittest.TestCase):
    def test_multica_description_uses_business_context_sections(self) -> None:
        description = _multica_description(
            {
                "id": "review_node",
                "label": "评审节点",
                "operation": "agent_task",
                "agent": {
                    "prompt": "请基于任务输入和上游节点结果输出中文评审结论。",
                    "context_policy": "provided_context_only",
                },
            },
            {
                "draft_node": {
                    "node": "draft_node",
                    "operation": "agent_task",
                    "duration_seconds": 1.5,
                    "payload": {"latest_comment": "上游草案结果"},
                }
            },
            {"task": "准备上线评审"},
            workflow_id="workflow-test",
            a2a_task_id="a2a-task-test",
        )

        self.assertIn("## 任务指令", description)
        self.assertIn("## 原始业务输入", description)
        self.assertIn("## 上游节点结果", description)
        self.assertIn("准备上线评审", description)
        self.assertIn("上游草案结果", description)

        self.assertNotIn("## A2A Context Payload", description)
        self.assertNotIn("Use only the A2A Context Payload", description)
        self.assertNotIn("parts[0]", description)
        self.assertNotIn("messageId", description)

    def test_multica_description_keeps_full_dependency_text_by_default(self) -> None:
        long_output = "full upstream business output " * 400

        description = _multica_description(
            {
                "id": "review_node",
                "label": "review",
                "operation": "agent_task",
                "agent": {
                    "prompt": "Review upstream result.",
                    "description_payload_limit": 200,
                },
            },
            {
                "draft_node": {
                    "node": "draft_node",
                    "operation": "agent_task",
                    "payload": {"latest_comment": long_output},
                }
            },
            {"task": "demo"},
            workflow_id="workflow-test",
            a2a_task_id="a2a-task-test",
        )

        self.assertIn(long_output, description)
        self.assertNotIn("...<truncated>", description)


if __name__ == "__main__":
    unittest.main()

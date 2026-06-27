import unittest

from web.agent.input_projection import extract_agent_input


class AgentInputProjectionTests(unittest.TestCase):
    def test_extracts_new_business_input_sections(self) -> None:
        task = {
            "description": """# MAOS 业务子任务

## 上下文策略

Policy: provided_context_only.

## 任务指令（必须执行）

请生成评审结论。

## 原始业务输入

```json
{"project": "知识库助手", "owner": "产品团队"}
```

## 上游节点结果

```json
{"plan": {"payload": {"latest_comment": "试点方案"}}}
```
"""
        }

        agent_input = extract_agent_input(task)

        self.assertIn("provided_context_only", agent_input["context_policy"])
        self.assertIn("请生成评审结论", agent_input["node_instruction"])
        self.assertIn("知识库助手", agent_input["original_business_input"])
        self.assertIn("试点方案", agent_input["upstream_node_results"])
        self.assertEqual(agent_input["graph_input_keys"], ["owner", "project"])
        self.assertEqual(agent_input["dependency_node_ids"], ["plan"])


if __name__ == "__main__":
    unittest.main()

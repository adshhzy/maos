import unittest

from maos_runtime.a2a.hermes_backend import _hermes_result
from maos_runtime.a2a.multica_backend import _multica_result


class ProviderFullResultArtifactTests(unittest.TestCase):
    def test_hermes_result_keeps_full_business_output(self) -> None:
        output = "H" * 3000

        result = _hermes_result(
            {
                "nodeId": "expert_node",
                "operation": "agent_task",
                "agentServiceTaskId": "agent-1",
                "resultTextLimit": 2500,
            },
            {"status": "done", "title": "Hermes task"},
            {"data": [{"content": output}]},
            None,
            None,
            1.0,
        )

        latest_comment = result["payload"]["latest_comment"]
        self.assertEqual(latest_comment, output)
        self.assertNotIn("...<truncated>", latest_comment)

    def test_multica_result_keeps_full_business_output(self) -> None:
        output = "M" * 3000

        result = _multica_result(
            {
                "nodeId": "expert_node",
                "operation": "agent_task",
                "agentServiceTaskId": "agent-2",
                "resultTextLimit": 2400,
            },
            {"status": "done", "title": "Multica task"},
            {"data": [{"content": output}]},
            None,
            None,
            1.0,
        )

        latest_comment = result["payload"]["latest_comment"]
        self.assertEqual(latest_comment, output)
        self.assertNotIn("...<truncated>", latest_comment)


if __name__ == "__main__":
    unittest.main()

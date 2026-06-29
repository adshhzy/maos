import unittest

from maos_runtime.workflows.json_dag import JsonDagWorkflow


class WorkflowLoopDependencyTests(unittest.TestCase):
    def test_revisit_injects_previous_self_execution(self) -> None:
        workflow = JsonDagWorkflow()
        workflow._visits = {"core_implementation": 2}
        workflow._latest_completed = {
            "api_contract_architect": {
                "status": "completed",
                "instance_id": "api_contract_architect#1",
                "a2a_task": {"id": "a2a-api", "artifacts": []},
            },
            "core_implementation": {
                "status": "completed",
                "instance_id": "core_implementation#1",
                "a2a_task": {"id": "a2a-core-v1", "artifacts": []},
            },
        }

        dependencies = workflow._dependency_executions_for_node(
            {"id": "core_implementation", "deps": ["api_contract_architect"]},
            {
                "quality_review_gate": {
                    "status": "completed",
                    "instance_id": "quality_review_gate#1",
                    "a2a_task": {"id": "a2a-review", "artifacts": []},
                }
            },
        )

        self.assertEqual(
            dependencies["core_implementation"]["instance_id"],
            "core_implementation#1",
        )
        self.assertEqual(
            dependencies["api_contract_architect"]["instance_id"],
            "api_contract_architect#1",
        )
        self.assertEqual(
            dependencies["quality_review_gate"]["instance_id"],
            "quality_review_gate#1",
        )

    def test_first_visit_does_not_inject_previous_self_execution(self) -> None:
        workflow = JsonDagWorkflow()
        workflow._visits = {"core_implementation": 1}
        workflow._latest_completed = {
            "core_implementation": {
                "status": "completed",
                "instance_id": "core_implementation#1",
                "a2a_task": {"id": "a2a-core-v1", "artifacts": []},
            }
        }

        dependencies = workflow._dependency_executions_for_node(
            {"id": "core_implementation", "deps": []},
            {},
        )

        self.assertNotIn("core_implementation", dependencies)


if __name__ == "__main__":
    unittest.main()

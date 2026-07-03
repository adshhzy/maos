import unittest

from maos_runtime.workflows.json_dag import (
    JsonDagWorkflow,
    _max_concurrent_nodes,
    _normalize_execution_policy,
)


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

    def test_serial_execution_policy_limits_ready_nodes_to_one(self) -> None:
        workflow = JsonDagWorkflow()
        workflow._execution_policy = _normalize_execution_policy(
            {"execution_policy": {"mode": "serial"}}
        )

        self.assertEqual(_max_concurrent_nodes(workflow._execution_policy), 1)
        self.assertEqual(
            workflow._limit_ready_nodes_by_execution_policy(["b", "c", "d"], {}),
            ["b"],
        )

    def test_serial_execution_policy_waits_when_one_node_is_running(self) -> None:
        workflow = JsonDagWorkflow()
        workflow._execution_policy = _normalize_execution_policy(
            {"execution_policy": {"mode": "serial"}}
        )
        running = {"a": object()}

        self.assertEqual(
            workflow._limit_ready_nodes_by_execution_policy(["b", "c"], running),
            [],
        )

    def test_default_parallel_policy_keeps_all_ready_nodes(self) -> None:
        workflow = JsonDagWorkflow()
        workflow._execution_policy = _normalize_execution_policy({})

        self.assertIsNone(_max_concurrent_nodes(workflow._execution_policy))
        self.assertEqual(
            workflow._limit_ready_nodes_by_execution_policy(["b", "c"], {}),
            ["b", "c"],
        )


if __name__ == "__main__":
    unittest.main()

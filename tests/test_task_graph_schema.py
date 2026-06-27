import json
import tempfile
import unittest
from pathlib import Path

from maos_runtime.graph.control_flow import _node_backend_for_display
from maos_runtime.graph.schema import GraphValidationError, validate_task_graph
from maos_runtime.graph.validate import main as validate_cli_main


class TaskGraphSchemaTests(unittest.TestCase):
    def test_valid_minimal_dag(self) -> None:
        validate_task_graph(
            {
                "id": "valid-dag",
                "nodes": [
                    {"id": "start", "operation": "emit"},
                    {"id": "finish", "deps": ["start"], "operation": "merge"},
                ],
            }
        )

    def test_missing_dependency_is_reported(self) -> None:
        with self.assertRaises(GraphValidationError) as context:
            validate_task_graph(
                {
                    "id": "bad-dependency",
                    "nodes": [
                        {"id": "finish", "deps": ["missing"], "operation": "merge"},
                    ],
                }
            )
        self.assertIn("Unknown dependency node id: missing", str(context.exception))

    def test_inferred_dag_cycle_is_reported(self) -> None:
        with self.assertRaises(GraphValidationError) as context:
            validate_task_graph(
                {
                    "id": "cycle",
                    "nodes": [
                        {"id": "a", "deps": ["b"]},
                        {"id": "b", "deps": ["a"]},
                    ],
                }
            )
        self.assertIn("DAG contains a cycle", str(context.exception))

    def test_cli_accepts_bom_and_graph_batch_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "batch.json"
            file_path.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "graphs": [
                            {
                                "id": "batch-graph",
                                "nodes": [{"id": "start", "operation": "emit"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            import sys

            old_argv = sys.argv
            try:
                sys.argv = ["validate", str(file_path)]
                self.assertEqual(validate_cli_main(), 0)
            finally:
                sys.argv = old_argv

    def test_claude_alias_displays_as_claude_backend(self) -> None:
        self.assertEqual(
            _node_backend_for_display({"agent": {"backend": "claude-cli"}}),
            "claude",
        )


if __name__ == "__main__":
    unittest.main()

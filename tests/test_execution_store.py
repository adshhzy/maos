import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from maos_runtime.a2a.artifact_store import store_dependency_artifact
from maos_runtime.a2a_task_store import store_idempotent_task
from maos_runtime.persistence import (
    execution_store_db_file,
    list_workflow_runs,
    load_evaluation_report,
    load_workflow_task_snapshot,
)
from maos_runtime.sandbox.task_archive import archive_task
from services.sandbox_api_service import (
    projected_task_snapshot_with_execution_store_fallback,
    task_snapshot_with_execution_store_fallback,
)


class ExecutionStoreTests(unittest.TestCase):
    def test_archive_task_projects_workflow_nodes_edges_and_evaluation(self) -> None:
        with _isolated_data_dir() as data_dir:
            task = {
                "task_id": "workflow-1",
                "workflow_id": "workflow-1",
                "graph_id": "graph-1",
                "graph_name": "Graph One",
                "graph_type": "control_flow",
                "status": "completed",
                "state": {
                    "graph_id": "graph-1",
                    "graph_name": "Graph One",
                    "graph_type": "control_flow",
                    "workflow_status": "completed",
                    "nodes": [
                        {
                            "id": "deterministic_evaluator",
                            "type": "agent",
                            "backend": "evaluator",
                            "status": "completed",
                            "current_instance_id": "deterministic_evaluator#1",
                            "a2a_task_id": "a2a-eval-1",
                        }
                    ],
                    "edges": [
                        {
                            "from": "a",
                            "to": "deterministic_evaluator",
                            "label": "done",
                            "taken_count": 1,
                            "skipped_count": 0,
                        }
                    ],
                    "instances": [
                        {
                            "id": "deterministic_evaluator#1",
                            "node_id": "deterministic_evaluator",
                            "visit": 1,
                            "status": "completed",
                            "backend": "evaluator",
                            "a2a_task_id": "a2a-eval-1",
                        }
                    ],
                    "human_interventions": [],
                    "results": {
                        "deterministic_evaluator": {
                            "benchmark_id": "async_ttl_cache_hard_concurrency",
                            "evaluation_task_id": "a2a-eval-1",
                            "winner": "multi",
                            "single": {
                                "score": 80,
                                "hidden_test_cases": [
                                    {"name": "test_a", "status": "failed"},
                                ],
                            },
                            "multi": {
                                "score": 100,
                                "hidden_test_cases": [
                                    {"name": "test_a", "status": "passed"},
                                ],
                            },
                        }
                    },
                },
                "result": {
                    "status": "completed",
                    "results": {
                        "deterministic_evaluator": {
                            "benchmark_id": "async_ttl_cache_hard_concurrency",
                            "evaluation_task_id": "a2a-eval-1",
                            "winner": "multi",
                            "single": {
                                "score": 80,
                                "hidden_test_cases": [
                                    {"name": "test_a", "status": "failed"},
                                ],
                            },
                            "multi": {
                                "score": 100,
                                "hidden_test_cases": [
                                    {"name": "test_a", "status": "passed"},
                                ],
                            },
                        }
                    },
                },
            }

            archive_task(task)

            db_file = Path(execution_store_db_file())
            self.assertEqual(db_file.parent, data_dir)
            self.assertTrue(db_file.is_file())
            self.assertEqual(load_workflow_task_snapshot("workflow-1")["graph_id"], "graph-1")
            self.assertEqual(list_workflow_runs(limit=1)[0]["workflow_id"], "workflow-1")
            report = load_evaluation_report("a2a-eval-1")
            self.assertIsNotNone(report)
            assert report is not None
            self.assertTrue(report["execution_store_source"])
            self.assertEqual(report["single"]["score"], 80)
            self.assertEqual(report["multi"]["score"], 100)
            self.assertEqual(report["test_matrix"]["tests"][0]["name"], "test_a")
            self.assertEqual(report["test_matrix"]["rows"][0]["statuses"]["test_a"], "failed")
            self.assertEqual(report["test_matrix"]["rows"][1]["statuses"]["test_a"], "passed")

            with sqlite3.connect(db_file) as conn:
                self.assertEqual(_count(conn, "workflow_runs"), 1)
                self.assertEqual(_count(conn, "graph_definitions"), 1)
                self.assertEqual(_count(conn, "node_instances"), 1)
                self.assertEqual(_count(conn, "edge_transitions"), 1)
                self.assertEqual(_count(conn, "evaluation_reports"), 1)
                self.assertEqual(_count(conn, "evaluation_test_cases"), 2)

    def test_provider_task_store_projects_invocation_and_artifact(self) -> None:
        with _isolated_data_dir() as data_dir:
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = str(data_dir / "provider_tasks.sqlite3")
            store_idempotent_task(
                {
                    "id": "a2a-task-node-1",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": {
                        "workflow_id": "workflow-provider",
                        "nodeId": "node_1",
                        "backend": "simulator",
                        "idempotencyKey": "workflow-provider:node_1#1:dispatch",
                    },
                    "artifacts": [
                        {
                            "artifactId": "artifact-node-1",
                            "name": "dag-node-result",
                            "parts": [
                                {
                                    "mediaType": "application/json",
                                    "data": {"payload": {"status": "completed"}},
                                }
                            ],
                        }
                    ],
                }
            )

            with sqlite3.connect(execution_store_db_file()) as conn:
                self.assertEqual(_count(conn, "provider_invocations"), 1)
                self.assertEqual(_count(conn, "artifacts"), 1)
                row = conn.execute("SELECT provider, node_id FROM provider_invocations").fetchone()
                self.assertEqual(row, ("simulator", "node_1"))

    def test_artifact_store_projects_metadata(self) -> None:
        with _isolated_data_dir():
            ref = store_dependency_artifact(
                {"payload": {"latest_comment": "hello"}},
                metadata={"workflow_id": "workflow-artifact", "nodeId": "node_a"},
                summary="hello",
            )

            with sqlite3.connect(execution_store_db_file()) as conn:
                row = conn.execute(
                    "SELECT artifact_id, workflow_id, kind, summary FROM artifacts"
                ).fetchone()
                self.assertEqual(row[0], ref["artifact_ref"])
                self.assertEqual(row[1], "workflow-artifact")
                self.assertEqual(row[2], "artifact")
                self.assertEqual(row[3], "hello")

    def test_task_snapshot_falls_back_to_execution_store(self) -> None:
        with _isolated_data_dir():
            long_output = "x" * 2000
            task = {
                "task_id": "workflow-fallback",
                "workflow_id": "workflow-fallback",
                "graph_id": "graph-fallback",
                "graph_name": "Fallback Graph",
                "status": "completed",
                "state": {
                    "nodes": [{"id": "node_a", "status": "completed"}],
                    "edges": [],
                    "results": {"node_a": {"latest_comment": long_output}},
                },
            }
            archive_task(task)

            loaded = task_snapshot_with_execution_store_fallback(
                _MissingTaskManager(),
                "workflow-fallback",
            )

            self.assertEqual(loaded["graph_id"], "graph-fallback")
            self.assertEqual(loaded["state"]["results"]["node_a"]["latest_comment"], long_output)

            projected = projected_task_snapshot_with_execution_store_fallback(
                _MissingTaskManager(),
                "workflow-fallback",
            )
            self.assertEqual(projected["_projection"]["name"], "task_detail")
            self.assertLess(
                len(projected["state"]["results"]["node_a"]["latest_comment"]),
                len(long_output),
            )

    def test_task_snapshot_primary_store_is_not_overwritten_by_live_shell(self) -> None:
        with _isolated_data_dir():
            archived = {
                "task_id": "workflow-primary",
                "workflow_id": "workflow-primary",
                "graph_id": "graph-primary",
                "graph_name": "Primary Store Graph",
                "status": "completed",
                "updated_at": "2026-01-01T00:00:00Z",
                "state": {
                    "nodes": [{"id": "node_a", "status": "completed"}],
                    "edges": [],
                    "results": {"node_a": {"latest_comment": "complete"}},
                },
            }
            archive_task(archived)

            loaded = task_snapshot_with_execution_store_fallback(
                _LiveShellManager(),
                "workflow-primary",
            )
            stored = load_workflow_task_snapshot("workflow-primary")

            self.assertEqual(loaded["status"], "completed")
            self.assertEqual(loaded["graph_name"], "Primary Store Graph")
            assert stored is not None
            self.assertEqual(stored["status"], "completed")
            self.assertEqual(stored["graph_name"], "Primary Store Graph")


class _isolated_data_dir:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name)
        self.previous = {
            key: os.environ.get(key)
            for key in (
                "MAOS_DATA_DIR",
                "MAOS_EXECUTION_DB_FILE",
                "A2A_PROVIDER_TASK_DB_FILE",
            )
        }
        os.environ["MAOS_DATA_DIR"] = str(self.path)
        os.environ.pop("MAOS_EXECUTION_DB_FILE", None)
        os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()


class _MissingTaskManager:
    def task_snapshot(self, task_id: str) -> dict:
        raise KeyError(task_id)


class _LiveShellManager:
    def task_snapshot(self, task_id: str) -> dict:
        return {
            "task_id": task_id,
            "workflow_id": task_id,
            "graph_id": task_id,
            "graph_name": "Live Shell",
            "status": "running",
            "updated_at": "2026-01-01T00:01:00Z",
            "state": {"nodes": [], "edges": []},
        }


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()

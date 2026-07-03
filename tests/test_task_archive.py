import json
import os
import tempfile
import unittest

from maos_runtime.sandbox.task_archive import (
    archive_workflow_result,
    load_archived_tasks,
    load_archived_task,
    merge_visible_tasks,
    prefer_archived_task,
)
from maos_runtime.workflows.activities import ACTIVITIES, archive_completed_workflow


class TaskArchiveTests(unittest.TestCase):
    def test_archive_workflow_result_persists_full_task_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MAOS_DATA_DIR")
            previous_db = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
            os.environ["MAOS_DATA_DIR"] = temp_dir
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = os.path.join(temp_dir, "maos_runtime.db")
            try:
                result = {
                    "graph_id": "graph-1",
                    "graph_name": "测试图",
                    "graph_type": "control_flow",
                    "status": "completed",
                    "results": {"node_a": {"latest_comment": "完整输出"}},
                    "state": {
                        "graph_id": "graph-1",
                        "graph_name": "测试图",
                        "workflow_status": "completed",
                        "nodes": [{"id": "node_a", "status": "completed"}],
                        "edges": [],
                        "levels": [["node_a"]],
                    },
                }

                response = archive_workflow_result(
                    workflow_id="task-graph-1-abc",
                    result=result,
                    submitted_at=1.0,
                    updated_at=2.0,
                )

                self.assertTrue(response["ok"])
                archived = load_archived_task("task-graph-1-abc")
                self.assertIsNotNone(archived)
                assert archived is not None
                self.assertEqual(archived["graph_name"], "测试图")
                self.assertEqual(archived["status"], "completed")
                self.assertEqual(archived["result"]["results"]["node_a"]["latest_comment"], "完整输出")

                with open(response["archive_path"], "r", encoding="utf-8") as handle:
                    on_disk = json.load(handle)
                self.assertEqual(on_disk["archived_by"], "workflow_completion_activity")
            finally:
                if previous is None:
                    os.environ.pop("MAOS_DATA_DIR", None)
                else:
                    os.environ["MAOS_DATA_DIR"] = previous
                if previous_db is None:
                    os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
                else:
                    os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_db

    def test_archive_activity_is_registered_for_worker(self) -> None:
        self.assertIn(archive_completed_workflow, ACTIVITIES)

    def test_prefers_archive_over_temporal_nondeterminism_shell(self) -> None:
        archived = {
            "task_id": "task-1",
            "status": "completed",
            "state": {"nodes": [{"id": "node_a"}], "edges": [{"from": "a", "to": "b"}]},
            "result": {"results": {"node_a": {"latest_comment": "ok"}}},
        }
        shell = {
            "task_id": "task-1",
            "status": "running",
            "state": {"nodes": [], "edges": []},
            "error": "[TMPRL1100] Nondeterminism error",
        }

        self.assertIs(prefer_archived_task(shell, archived), archived)

    def test_visible_tasks_keep_archived_graph_when_live_task_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MAOS_DATA_DIR")
            previous_db = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
            os.environ["MAOS_DATA_DIR"] = temp_dir
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = os.path.join(temp_dir, "maos_runtime.db")
            try:
                archive_workflow_result(
                    workflow_id="task-1",
                    result={
                        "graph_id": "graph-1",
                        "graph_name": "Full graph",
                        "status": "completed",
                        "state": {
                            "graph_id": "graph-1",
                            "graph_name": "Full graph",
                            "workflow_status": "completed",
                            "nodes": [{"id": "node_a", "status": "completed"}],
                            "edges": [],
                            "levels": [["node_a"]],
                        },
                        "results": {"node_a": {"latest_comment": "ok"}},
                    },
                    submitted_at=1,
                    updated_at=2,
                )
                rows = merge_visible_tasks(
                    [
                        {
                            "task_id": "task-1",
                            "workflow_id": "task-1",
                            "graph_id": "task-1",
                            "graph_name": "task-1",
                            "status": "running",
                            "state": {"nodes": [], "edges": []},
                            "error": "[TMPRL1100] Nondeterminism error",
                        }
                    ]
                )

                self.assertEqual(rows[0]["graph_name"], "Full graph")
                self.assertEqual(len(rows[0]["state"]["nodes"]), 1)
            finally:
                if previous is None:
                    os.environ.pop("MAOS_DATA_DIR", None)
                else:
                    os.environ["MAOS_DATA_DIR"] = previous
                if previous_db is None:
                    os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
                else:
                    os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_db

    def test_live_closed_status_overrides_stale_running_archive(self) -> None:
        archived = {
            "task_id": "task-1",
            "status": "running",
            "state": {
                "workflow_status": "running",
                "nodes": [
                    {"id": "node_a", "status": "completed"},
                    {"id": "node_b", "status": "suspended"},
                ],
                "edges": [{"from": "node_a", "to": "node_b"}],
            },
            "result": None,
            "error": None,
        }
        live_shell = {
            "task_id": "task-1",
            "status": "terminated",
            "state": {"nodes": [], "edges": []},
            "error": "terminated by user",
        }

        selected = prefer_archived_task(live_shell, archived)

        self.assertEqual(selected["status"], "terminated")
        self.assertEqual(selected["state"]["workflow_status"], "terminated")
        self.assertEqual(selected["error"], "terminated by user")
        self.assertEqual(selected["state"]["nodes"][0]["status"], "completed")
        self.assertEqual(selected["state"]["nodes"][1]["status"], "cancelled")

    def test_load_archived_task_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MAOS_DATA_DIR")
            previous_db = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
            os.environ["MAOS_DATA_DIR"] = temp_dir
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = os.path.join(temp_dir, "maos_runtime.db")
            try:
                archive_dir = os.path.join(temp_dir, "task-archive")
                os.makedirs(archive_dir, exist_ok=True)
                path = os.path.join(archive_dir, "task-1.json")
                with open(path, "w", encoding="utf-8-sig") as handle:
                    json.dump({"task_id": "task-1", "status": "terminated"}, handle)

                archived = load_archived_task("task-1")

                self.assertIsNotNone(archived)
                assert archived is not None
                self.assertEqual(archived["status"], "terminated")
            finally:
                if previous is None:
                    os.environ.pop("MAOS_DATA_DIR", None)
                else:
                    os.environ["MAOS_DATA_DIR"] = previous
                if previous_db is None:
                    os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
                else:
                    os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_db

    def test_archived_closed_graph_wins_over_newer_recovered_running_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MAOS_DATA_DIR")
            previous_db = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
            os.environ["MAOS_DATA_DIR"] = temp_dir
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = os.path.join(temp_dir, "maos_runtime.db")
            try:
                archive_workflow_result(
                    workflow_id="task-1",
                    result={
                        "graph_id": "graph-1",
                        "graph_name": "Full graph",
                        "status": "terminated",
                        "state": {
                            "graph_id": "graph-1",
                            "graph_name": "Full graph",
                            "workflow_status": "terminated",
                            "nodes": [{"id": "node_a", "status": "cancelled"}],
                            "edges": [],
                            "levels": [["node_a"]],
                        },
                    },
                    submitted_at=1,
                    updated_at=2,
                )

                from maos_runtime.a2a_task_store import store_task

                store_task(
                    {
                        "id": "provider-task-1",
                        "status": {"state": "TASK_STATE_CANCELED"},
                        "metadata": {
                            "workflowId": "task-1",
                            "nodeId": "node_a",
                            "backend": "claude",
                        },
                    }
                )

                tasks = load_archived_tasks(limit=None)
                selected = [task for task in tasks if task.get("task_id") == "task-1"][0]

                self.assertEqual(selected["status"], "terminated")
                self.assertEqual(selected["state"]["workflow_status"], "terminated")
            finally:
                if previous is None:
                    os.environ.pop("MAOS_DATA_DIR", None)
                else:
                    os.environ["MAOS_DATA_DIR"] = previous
                if previous_db is None:
                    os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
                else:
                    os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_db

    def test_stale_suspended_archive_is_refreshed_from_completed_provider_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MAOS_DATA_DIR")
            previous_db = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
            os.environ["MAOS_DATA_DIR"] = temp_dir
            os.environ["A2A_PROVIDER_TASK_DB_FILE"] = os.path.join(temp_dir, "maos_runtime.db")
            try:
                archive_workflow_result(
                    workflow_id="task-evaluator-1",
                    result={
                        "graph_id": "evaluate-graph",
                        "graph_name": "Evaluator graph",
                        "status": "running",
                        "state": {
                            "graph_id": "evaluate-graph",
                            "graph_name": "Evaluator graph",
                            "workflow_status": "running",
                            "nodes": [
                                {
                                    "id": "deterministic_evaluator",
                                    "status": "suspended",
                                    "a2a_state": "TASK_STATE_WORKING",
                                    "a2a_task_id": "provider-eval-1",
                                }
                            ],
                            "edges": [],
                            "levels": [["deterministic_evaluator"]],
                        },
                    },
                    submitted_at=1,
                    updated_at=2,
                )

                from maos_runtime.a2a_task_store import store_task

                store_task(
                    {
                        "id": "provider-eval-1",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "timestamp": "2026-07-01T04:09:13Z",
                        },
                        "artifacts": [{"parts": [{"data": {"single_score": 82.5, "multi_score": 91.25}}]}],
                        "metadata": {
                            "idempotencyKey": "task-evaluator-1:deterministic_evaluator#1:dispatch",
                            "workflow_id": "task-evaluator-1",
                            "nodeId": "deterministic_evaluator",
                            "backend": "evaluator",
                            "finishedAt": "2026-07-01T04:09:13Z",
                        },
                    }
                )

                archived = load_archived_task("task-evaluator-1")

                self.assertIsNotNone(archived)
                assert archived is not None
                node = archived["state"]["nodes"][0]
                self.assertEqual(archived["status"], "completed")
                self.assertEqual(archived["state"]["workflow_status"], "completed")
                self.assertEqual(node["status"], "completed")
                self.assertEqual(node["a2a_state"], "TASK_STATE_COMPLETED")
                self.assertEqual(node["artifact_count"], 1)
            finally:
                if previous is None:
                    os.environ.pop("MAOS_DATA_DIR", None)
                else:
                    os.environ["MAOS_DATA_DIR"] = previous
                if previous_db is None:
                    os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
                else:
                    os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_db


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest

from maos_runtime.sandbox.task_archive import (
    archive_workflow_result,
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


if __name__ == "__main__":
    unittest.main()

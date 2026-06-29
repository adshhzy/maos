import os
import tempfile
import unittest
from unittest import mock

from maos_runtime.a2a_task_store import (
    TASKS,
    TASKS_LOCK,
    clear_persistent_tasks,
    load_invocation_records,
    store_idempotent_task,
    store_task,
    task_for_idempotency_key,
)


class A2ATaskStoreTests(unittest.TestCase):
    def test_empty_snapshot_does_not_overwrite_persisted_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = os.path.join(tmp, "maos_runtime.db")
            with mock.patch.dict(
                os.environ,
                {
                    "A2A_PROVIDER_TASK_DB_FILE": db_file,
                    "A2A_DISABLE_JSON_INVOCATION_MIRROR": "1",
                },
            ):
                clear_persistent_tasks()
                completed = {
                    "id": "a2a-task-eval",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [{"artifactId": "artifact-1", "name": "dag-node-result"}],
                    "metadata": {
                        "idempotencyKey": "workflow:node#1:dispatch",
                        "workflow_id": "workflow",
                        "nodeId": "node",
                        "backend": "evaluator",
                    },
                }
                stale = {
                    **completed,
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [],
                }

                store_idempotent_task(completed)
                store_idempotent_task(stale)

                data = load_invocation_records()
                record = data["workflow:node#1:dispatch"]
                self.assertTrue(record["result_artifact_created"])
                self.assertEqual(record["task"]["artifacts"][0]["artifactId"], "artifact-1")

    def test_task_is_recovered_from_sqlite_after_memory_cache_clear(self) -> None:
        task = {
            "id": "a2a-task-sqlite",
            "status": {"state": "TASK_STATE_WORKING"},
            "artifacts": [],
            "metadata": {
                "idempotencyKey": "workflow:node#1:dispatch",
                "workflow_id": "workflow",
                "nodeId": "node",
                "backend": "simulator",
            },
        }

        store_task(task)
        with TASKS_LOCK:
            dict.clear(TASKS)

        recovered = task_for_idempotency_key("workflow:node#1:dispatch")

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["id"], "a2a-task-sqlite")
        with TASKS_LOCK:
            self.assertIn("a2a-task-sqlite", TASKS)

    def test_load_invocation_records_reads_sqlite(self) -> None:
        task = {
            "id": "a2a-task-sqlite-records",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"artifactId": "artifact-1", "name": "dag-node-result"}],
            "metadata": {
                "idempotencyKey": "workflow:node#2:dispatch",
                "workflow_id": "workflow",
                "nodeId": "node",
                "backend": "evaluator",
            },
        }

        store_idempotent_task(task)
        records = load_invocation_records()

        self.assertIn("workflow:node#2:dispatch", records)
        self.assertEqual(
            records["workflow:node#2:dispatch"]["a2a_task_id"],
            "a2a-task-sqlite-records",
        )


if __name__ == "__main__":
    unittest.main()

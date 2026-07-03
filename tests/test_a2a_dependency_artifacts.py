import os
import tempfile
import unittest

from maos_runtime.a2a.messages import (
    _dependency_artifacts_for_message,
    _dependency_results_from_artifacts,
)


class A2ADependencyArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        self._previous_api_base = os.environ.get("SANDBOX_API_BASE")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAOS_DATA_DIR"] = self._tmp.name
        os.environ["SANDBOX_API_BASE"] = "http://127.0.0.1:8766"

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._previous_data_dir is None:
            os.environ.pop("MAOS_DATA_DIR", None)
        else:
            os.environ["MAOS_DATA_DIR"] = self._previous_data_dir
        if self._previous_api_base is None:
            os.environ.pop("SANDBOX_API_BASE", None)
        else:
            os.environ["SANDBOX_API_BASE"] = self._previous_api_base

    def test_dependency_artifacts_default_to_external_refs(self) -> None:
        long_output = "A" * 4000
        artifact = _artifact_with_payload(
            {
                "latest_comment": long_output,
                "stdout": long_output,
                "comments": [{"text": long_output}],
                "status": "done",
            }
        )

        refs = _dependency_artifacts_for_message(
            [artifact],
            workflow_id="workflow-a",
            consumer_node_id="downstream",
        )
        payload = _dependency_results_from_artifacts(refs)["upstream"]["payload"]

        self.assertIn("artifact_ref", payload)
        self.assertIn("uri", payload)
        self.assertIn("content_hash", payload)
        self.assertEqual(payload["mime_type"], "application/json")
        self.assertGreater(payload["size"], len(long_output))
        self.assertIn("summary", payload)
        self.assertNotIn("latest_comment", payload)
        self.assertNotIn("stdout", payload)
        self.assertTrue(refs[0]["metadata"]["compactDependencyArtifact"])
        self.assertEqual(refs[0]["metadata"]["dependencyTransferMode"], "ref")
        self.assertEqual(refs[0]["metadata"]["consumerWorkflowId"], "workflow-a")

    def test_dependency_artifact_refs_can_be_resolved_by_artifact_api_layer(self) -> None:
        long_output = "B" * 4000
        artifact = _artifact_with_payload(
            {"latest_comment": long_output, "status": "done"},
            workflow_id="workflow-a",
        )

        refs = _dependency_artifacts_for_message([artifact], workflow_id="workflow-a")
        payload = _dependency_results_from_artifacts(
            refs,
            resolve_refs=True,
            expected_workflow_id="workflow-a",
        )["upstream"]["payload"]

        self.assertEqual(payload["summary"], long_output)
        self.assertIn("artifact_ref", payload)
        self.assertIn("uri", payload)

    def test_dependency_artifact_refs_are_scoped_by_workflow_run(self) -> None:
        artifact_a = _artifact_with_payload(
            {"latest_comment": "same output"},
            workflow_id="workflow-a",
        )
        artifact_b = _artifact_with_payload(
            {"latest_comment": "same output"},
            workflow_id="workflow-b",
        )

        refs_a = _dependency_artifacts_for_message([artifact_a], workflow_id="workflow-a")
        refs_b = _dependency_artifacts_for_message([artifact_b], workflow_id="workflow-b")

        payload_a = refs_a[0]["parts"][0]["data"]["payload"]
        payload_b = refs_b[0]["parts"][0]["data"]["payload"]
        self.assertNotEqual(payload_a["artifact_ref"], payload_b["artifact_ref"])
        self.assertEqual(payload_a["content_hash"], payload_b["content_hash"])

    def test_dependency_artifact_ref_from_other_workflow_is_not_expanded(self) -> None:
        long_output = "foreign output" * 500
        artifact = _artifact_with_payload(
            {"latest_comment": long_output},
            workflow_id="workflow-a",
        )

        refs = _dependency_artifacts_for_message([artifact], workflow_id="workflow-a")
        payload = _dependency_results_from_artifacts(
            refs,
            resolve_refs=True,
            expected_workflow_id="workflow-b",
        )["upstream"]["payload"]

        self.assertNotEqual(payload.get("summary"), long_output)
        self.assertEqual(
            payload["artifact_scope_error"],
            "artifact_ref belongs to a different workflow run and was not expanded",
        )
        self.assertEqual(payload["artifact_workflow_id"], "workflow-a")
        self.assertEqual(payload["expected_workflow_id"], "workflow-b")

    def test_inline_mode_keeps_previous_compact_payload_shape(self) -> None:
        long_output = "C" * 4000
        artifact = _artifact_with_payload(
            {
                "latest_comment": long_output,
                "stdout": long_output,
                "comments": [{"text": long_output}],
                "status": "done",
            }
        )

        inline = _dependency_artifacts_for_message([artifact], transfer_mode="inline")
        payload = _dependency_results_from_artifacts(inline)["upstream"]["payload"]

        self.assertEqual(payload["summary"], long_output)
        self.assertEqual(payload["status"], "done")
        self.assertNotIn("artifact_ref", payload)
        self.assertNotIn("latest_comment", payload)
        self.assertEqual(inline[0]["metadata"]["dependencyTransferMode"], "inline")


def _artifact_with_payload(payload: dict, *, workflow_id: str | None = None) -> dict:
    metadata = {"nodeId": "upstream"}
    if workflow_id:
        metadata["workflow_id"] = workflow_id
    return {
        "artifactId": "artifact-upstream",
        "name": "dag-node-result",
        "parts": [
            {
                "mediaType": "application/json",
                "data": {
                    "node": "upstream",
                    "operation": "agent_task",
                    "duration_seconds": 1.2,
                    "payload": payload,
                },
            }
        ],
        "metadata": metadata,
    }


if __name__ == "__main__":
    unittest.main()

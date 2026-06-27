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

        refs = _dependency_artifacts_for_message([artifact])
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

    def test_dependency_artifact_refs_can_be_resolved_by_artifact_api_layer(self) -> None:
        long_output = "B" * 4000
        artifact = _artifact_with_payload({"latest_comment": long_output, "status": "done"})

        refs = _dependency_artifacts_for_message([artifact])
        payload = _dependency_results_from_artifacts(refs, resolve_refs=True)["upstream"]["payload"]

        self.assertEqual(payload["summary"], long_output)
        self.assertIn("artifact_ref", payload)
        self.assertIn("uri", payload)

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


def _artifact_with_payload(payload: dict) -> dict:
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
        "metadata": {"nodeId": "upstream"},
    }


if __name__ == "__main__":
    unittest.main()

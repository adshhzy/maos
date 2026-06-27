import unittest

from simulator.simulator_backend import _build_context, _resolve_value


class SimulatorDependencyContextTests(unittest.TestCase):
    def test_compacted_latest_comment_summary_remains_addressable(self) -> None:
        context = _build_context(
            {},
            {
                "review": {
                    "node": "review",
                    "payload": {
                        "status": "completed",
                        "summary": "compact agent output",
                        "_omitted_dependency_fields": ["latest_comment", "stdout"],
                    },
                }
            },
            {},
        )

        self.assertEqual(
            _resolve_value("$deps.review.latest_comment", context),
            "compact agent output",
        )
        self.assertEqual(
            _resolve_value("$deps.review.stdout", context),
            "compact agent output",
        )


if __name__ == "__main__":
    unittest.main()

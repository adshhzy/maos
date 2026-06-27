import unittest

from maos_runtime.a2a.hermes_backend import _hermes_output_validation_error


class HermesOutputValidationTests(unittest.TestCase):
    def test_json_like_output_no_longer_hard_fails(self) -> None:
        output = '{"summary": "回答下方明确显示"来源"部分，用户可点击查看原文"}'

        error = _hermes_output_validation_error(
            {"expectedOutputFormat": "json_object"},
            output,
        )

        self.assertIsNone(error)

    def test_empty_output_still_fails(self) -> None:
        self.assertEqual(
            _hermes_output_validation_error({}, ""),
            "Hermes returned an empty final output",
        )

    def test_provider_runtime_error_output_fails_node(self) -> None:
        error = _hermes_output_validation_error(
            {},
            "API call failed after 3 retries: HTTP 429: Too many requests, "
            "the rate limit is 500000 tokens per minute.",
        )

        self.assertIsNotNone(error)
        self.assertIn("provider/runtime failure", error)


if __name__ == "__main__":
    unittest.main()

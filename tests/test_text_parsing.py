import unittest

from maos_runtime.a2a.text_parsing import _extract_structured_agent_output


class TextParsingTests(unittest.TestCase):
    def test_strict_decision_output_requires_complete_json(self) -> None:
        text = "There are risks, but approved with risk. {\"decision\":\"approved\"}"

        parsed = _extract_structured_agent_output(
            text,
            allow_text_decision=False,
            require_complete_decision=True,
        )

        self.assertEqual(parsed, {})

    def test_strict_decision_output_accepts_complete_risk_pass_json(self) -> None:
        text = (
            '{"decision":"approved_with_risk","reason":"last review round",'
            '"required_changes":["follow up"],"risks":["known limitation"],'
            '"confidence":0.7}'
        )

        parsed = _extract_structured_agent_output(
            text,
            allow_text_decision=False,
            require_complete_decision=True,
        )

        self.assertEqual(parsed["decision"], "approved_with_risk")
        self.assertEqual(parsed["risks"], ["known limitation"])

    def test_required_structured_fields_are_enforced(self) -> None:
        text = (
            '{"decision":"needs_revision","reason":"missing boundary tests",'
            '"required_changes":["fix implementation"],"risks":[]}'
        )

        parsed = _extract_structured_agent_output(
            text,
            allow_text_decision=False,
            require_complete_decision=True,
            required_fields=[
                "implementation_required_changes",
                "test_required_changes",
                "acceptance_checks",
            ],
        )

        self.assertEqual(parsed, {})

    def test_required_structured_fields_accept_complete_payload(self) -> None:
        text = (
            '{"decision":"needs_revision","reason":"missing boundary tests",'
            '"required_changes":["fix implementation"],'
            '"implementation_required_changes":["handle expired LRU entries"],'
            '"test_required_changes":["add concurrent single-flight assertion"],'
            '"acceptance_checks":["same-key concurrent calls invoke underlying function once"],'
            '"risks":[]}'
        )

        parsed = _extract_structured_agent_output(
            text,
            allow_text_decision=False,
            require_complete_decision=True,
            required_fields=[
                "implementation_required_changes",
                "test_required_changes",
                "acceptance_checks",
            ],
        )

        self.assertEqual(parsed["decision"], "needs_revision")
        self.assertEqual(
            parsed["implementation_required_changes"],
            ["handle expired LRU entries"],
        )


if __name__ == "__main__":
    unittest.main()

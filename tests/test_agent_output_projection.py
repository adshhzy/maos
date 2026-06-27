import unittest

from web.agent.output_projection import select_final_output


class AgentOutputProjectionTests(unittest.TestCase):
    def test_final_output_is_not_truncated(self) -> None:
        content = "最终输出\n" + ("完整内容。" * 3000)

        output = select_final_output([
            {
                "id": "comment-1",
                "author_type": "agent",
                "content": content,
                "created_at": "2026-06-27T00:00:00Z",
            }
        ])

        self.assertEqual(output["content"], content)
        self.assertEqual(output["chars"], len(content))


if __name__ == "__main__":
    unittest.main()

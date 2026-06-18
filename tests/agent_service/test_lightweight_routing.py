from __future__ import annotations

import unittest

from services.agent_service.bootstrap_patch import should_patch_compact_bootstrap
from services.agent_service.main import (
    _initial_multica_status,
    _lightweight_chat_prompt,
    _should_use_lightweight,
)
from services.agent_service.schemas import TaskCreateRequest


class LightweightRoutingTests(unittest.TestCase):
    def test_provided_context_only_keeps_real_agent_runtime(self) -> None:
        request = TaskCreateRequest(
            title="Architecture node",
            agent_key="architect",
            metadata={
                "context_policy": "provided_context_only",
            },
        )

        self.assertFalse(_should_use_lightweight(request))

    def test_explicit_lightweight_runtime_uses_hermes(self) -> None:
        request = TaskCreateRequest(
            title="Architecture node",
            agent_key="architect",
            metadata={
                "context_policy": "provided_context_only",
                "runtime_profile": "lightweight",
            },
        )

        self.assertTrue(_should_use_lightweight(request))

    def test_full_multica_execution_mode_disables_lightweight_runtime(self) -> None:
        request = TaskCreateRequest(
            title="Repository architecture node",
            agent_key="architect",
            metadata={
                "context_policy": "provided_context_only",
                "execution_mode": "multica",
            },
        )

        self.assertFalse(_should_use_lightweight(request))

    def test_compact_maos_runtime_keeps_real_agent_and_patches_bootstrap(self) -> None:
        request = TaskCreateRequest(
            title="Compact real-agent node",
            agent_key="architect",
            metadata={
                "maos_task": True,
                "runtime_profile": "maos_compact_agent",
                "comment_history_policy": "disabled",
            },
        )

        self.assertFalse(_should_use_lightweight(request))
        self.assertTrue(should_patch_compact_bootstrap(request.metadata))

    def test_lightweight_prompt_preserves_requested_role(self) -> None:
        request = TaskCreateRequest(
            title="Architecture node",
            description="Produce a short integration plan.",
            agent_key="architect",
        )

        prompt = _lightweight_chat_prompt(request)

        self.assertIn("Requested role: architect", prompt)
        self.assertIn("Use only the task description and A2A context payload", prompt)
        self.assertIn("Produce a short integration plan.", prompt)

    def test_in_progress_multica_task_starts_after_create(self) -> None:
        self.assertEqual(_initial_multica_status("in_progress"), "backlog")
        self.assertEqual(_initial_multica_status("todo"), "todo")


if __name__ == "__main__":
    unittest.main()

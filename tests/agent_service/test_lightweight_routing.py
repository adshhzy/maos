from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.agent_service.bootstrap_patch import should_patch_compact_bootstrap
from services.agent_service.hermes import _resolve_hermes_executable
from services.agent_service.main import (
    _create_lightweight_chat_task,
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
        self.assertIn("Use only the task description, original task input, and upstream node results", prompt)
        self.assertIn("Produce a short integration plan.", prompt)

    def test_lightweight_task_preserves_requested_agent_key(self) -> None:
        request = TaskCreateRequest(
            title="Business analysis node",
            description="Return JSON.",
            agent_key="business_analyst",
            metadata={"execution_mode": "hermes_oneshot"},
        )

        class BackgroundTasksStub:
            def __init__(self) -> None:
                self.calls = []

            def add_task(self, *args, **kwargs) -> None:
                self.calls.append((args, kwargs))

        import services.agent_service.main as main

        original_or_502 = main._or_502
        try:
            main._or_502 = lambda _callable, **kwargs: {"id": "task-1", **kwargs}
            task = _create_lightweight_chat_task(request, BackgroundTasksStub())
        finally:
            main._or_502 = original_or_502

        self.assertEqual(task["_lightweight_chat"]["agent_key"], "business_analyst")

    def test_cmd_wrapper_resolves_to_hermes_exe(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "hermes.cmd"
            target = root / ".venv" / "Scripts" / "hermes.exe"
            target.parent.mkdir(parents=True)
            configured.write_text("@echo off\n", encoding="utf-8")
            target.write_text("", encoding="utf-8")

            self.assertEqual(_resolve_hermes_executable(str(configured)), str(target))

    def test_in_progress_multica_task_starts_after_create(self) -> None:
        self.assertEqual(_initial_multica_status("in_progress"), "backlog")
        self.assertEqual(_initial_multica_status("todo"), "todo")


if __name__ == "__main__":
    unittest.main()

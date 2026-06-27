"""A2A-compatible Agent runtime dispatch package."""

from maos_runtime.a2a.runtime import (
    cancel_task,
    complete_task_from_agent_callback,
    extract_result_from_task,
    get_agent_card,
    get_task,
    poll_task,
    provider_capabilities,
    registered_agent_backends,
    register_agent_provider,
    resume_task_with_human_response,
    send_message,
    task_artifacts,
    task_events,
)

__all__ = [
    "cancel_task",
    "complete_task_from_agent_callback",
    "extract_result_from_task",
    "get_agent_card",
    "get_task",
    "poll_task",
    "provider_capabilities",
    "registered_agent_backends",
    "register_agent_provider",
    "resume_task_with_human_response",
    "send_message",
    "task_artifacts",
    "task_events",
]

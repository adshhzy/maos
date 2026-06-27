"""A2A provider backend 常量。"""

SIMULATOR_BACKEND = "simulator"
MULTICA_BACKEND = "multica"
HERMES_BACKEND = "hermes"
CODEX_BACKEND = "codex"
CLAUDE_BACKEND = "claude"

MULTICA_COMPLETED_STATUSES = {"done", "in_review"}
MULTICA_FAILED_STATUSES = {"blocked", "cancelled", "canceled"}

PROVIDED_CONTEXT_ONLY = "provided_context_only"
LIGHTWEIGHT_RUNTIME_PROFILE = "lightweight"
LIGHTWEIGHT_MULTICA_AGENT_KEY = "general_chat"

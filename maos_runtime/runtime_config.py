"""持久化执行层的运行时配置。

这里集中管理 Sandbox、Simulator、Multica/AgentService 的地址和轮询参数，
避免 provider 代码到处直接读取环境变量。
"""

import os


def simulator_api_base() -> str:
    return os.environ.get("SIMULATOR_API_BASE", "http://127.0.0.1:8767")


def sandbox_api_base() -> str:
    return os.environ.get("SANDBOX_API_BASE", "http://127.0.0.1:8765")


def agent_service_api_base() -> str:
    return os.environ.get("AGENT_SERVICE_API_BASE", "http://127.0.0.1:8091")


def agent_poll_seconds() -> float:
    return float(os.environ.get("AGENT_SERVICE_POLL_SECONDS", "30"))


def agent_result_recent_comments() -> int:
    return int(os.environ.get("AGENT_SERVICE_RESULT_RECENT_COMMENTS", "3"))


def agent_result_text_limit() -> int:
    return int(os.environ.get("AGENT_SERVICE_RESULT_TEXT_LIMIT", "1200"))


def agent_message_text_limit() -> int:
    return int(os.environ.get("AGENT_SERVICE_MESSAGE_TEXT_LIMIT", "1000"))


def agent_fetch_run_messages() -> bool:
    value = os.environ.get("AGENT_SERVICE_FETCH_RUN_MESSAGES", "false")
    return value.lower() in {"1", "true", "yes", "on"}


def codex_cli_bin() -> str:
    return os.environ.get("CODEX_CLI_BIN", "codex")


def codex_workdir() -> str:
    return os.environ.get("CODEX_CLI_WORKDIR", os.getcwd())


def codex_timeout_seconds() -> float:
    return float(os.environ.get("CODEX_CLI_TIMEOUT_SECONDS", "1800"))


def codex_sandbox_mode() -> str:
    return os.environ.get("CODEX_CLI_SANDBOX", "read-only")


def codex_approval_policy() -> str:
    return os.environ.get("CODEX_CLI_APPROVAL_POLICY", "never")


def claude_cli_bin() -> str:
    return os.environ.get("CLAUDE_CLI_BIN", "claude")


def claude_model() -> str:
    return os.environ.get("CLAUDE_CLI_MODEL", "glm-5.1")


def claude_workdir() -> str:
    return os.environ.get("CLAUDE_CLI_WORKDIR", os.getcwd())


def claude_timeout_seconds() -> float:
    return float(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", "1800"))


def claude_permission_mode() -> str:
    return os.environ.get("CLAUDE_CLI_PERMISSION_MODE", "acceptEdits")


def claude_bare_enabled() -> bool:
    value = os.environ.get("CLAUDE_CLI_BARE", "true")
    return value.lower() in {"1", "true", "yes", "on"}


def claude_tools() -> str | None:
    return os.environ.get("CLAUDE_CLI_TOOLS", "Bash,WebFetch")


def claude_allowed_tools() -> str | None:
    return os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", "Bash(curl *),Bash(wget *),WebFetch")


def codex_warmup_enabled() -> bool:
    value = os.environ.get("CODEX_PROVIDER_WARMUP_ENABLED", "true")
    return value.lower() in {"1", "true", "yes", "on"}


def codex_warmup_wait() -> bool:
    value = os.environ.get("CODEX_PROVIDER_WARMUP_WAIT", "false")
    return value.lower() in {"1", "true", "yes", "on"}


def codex_warmup_timeout_seconds() -> float:
    return float(os.environ.get("CODEX_PROVIDER_WARMUP_TIMEOUT_SECONDS", "240"))


def codex_warmup_prompt() -> str:
    return os.environ.get("CODEX_PROVIDER_WARMUP_PROMPT", "Reply with exactly: codex-warmup-ok")


def codex_runtime_pool_mode() -> str:
    return os.environ.get("CODEX_PROVIDER_POOL_MODE", "exec-warmup").strip().lower()


def codex_app_server_url() -> str:
    return os.environ.get("CODEX_APP_SERVER_URL", "ws://127.0.0.1:9879")


def http_timeout_seconds(base_url: str) -> float:
    if base_url.rstrip("/") == agent_service_api_base().rstrip("/"):
        return float(os.environ.get("AGENT_SERVICE_HTTP_TIMEOUT_SECONDS", "75"))
    return float(os.environ.get("SIMULATOR_HTTP_TIMEOUT_SECONDS", "15"))

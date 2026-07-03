"""持久化执行层的运行时配置。

这里集中管理 Sandbox、Simulator、Multica/AgentService 的地址和轮询参数，
避免 provider 代码到处直接读取环境变量。
"""

import os
from pathlib import Path


def _load_dotenv_once() -> None:
    if os.environ.get("MAOS_DOTENV_LOADED") == "1":
        return
    os.environ["MAOS_DOTENV_LOADED"] = "1"
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    _load_dotenv_once()
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    value = _windows_env(name)
    if value not in (None, ""):
        os.environ.setdefault(name, value)
        return value
    return default


def _windows_env(name: str) -> str | None:
    if os.name != "nt":
        return None
    if os.environ.get("MAOS_DISABLE_WINDOWS_ENV_FALLBACK", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    try:
        import winreg
    except Exception:
        return None
    locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for root, subkey in locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if value not in (None, ""):
            return str(value)
    return None


def simulator_api_base() -> str:
    return _env("SIMULATOR_API_BASE", "http://127.0.0.1:8767")


def sandbox_api_base() -> str:
    return _env("SANDBOX_API_BASE", "http://127.0.0.1:8765")


def agent_service_api_base() -> str:
    return _env("AGENT_SERVICE_API_BASE", "http://127.0.0.1:8091")


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


def claude_huawei_cli_bin() -> str:
    return os.environ.get("CLAUDE_HUAWEI_CLI_BIN", claude_cli_bin())


def claude_huawei_model() -> str:
    return os.environ.get("CLAUDE_HUAWEI_CLI_MODEL", "deepseek-v3.2")


def claude_huawei_workdir() -> str:
    return os.environ.get("CLAUDE_HUAWEI_CLI_WORKDIR", claude_workdir())


def claude_huawei_timeout_seconds() -> float:
    return float(os.environ.get("CLAUDE_HUAWEI_CLI_TIMEOUT_SECONDS", str(claude_timeout_seconds())))


def claude_huawei_permission_mode() -> str:
    return os.environ.get("CLAUDE_HUAWEI_CLI_PERMISSION_MODE", claude_permission_mode())


def claude_huawei_bare_enabled() -> bool:
    value = os.environ.get("CLAUDE_HUAWEI_CLI_BARE")
    if value is None:
        return claude_bare_enabled()
    return value.lower() in {"1", "true", "yes", "on"}


def claude_huawei_tools() -> str | None:
    value = os.environ.get("CLAUDE_HUAWEI_CLI_TOOLS")
    return value if value else claude_tools()


def claude_huawei_allowed_tools() -> str | None:
    value = os.environ.get("CLAUDE_HUAWEI_CLI_ALLOWED_TOOLS")
    return value if value else claude_allowed_tools()


def claude_huawei_anthropic_base_url() -> str:
    return _env("CLAUDE_HUAWEI_ANTHROPIC_BASE_URL", "")


def claude_huawei_auth_token() -> str:
    return (
        _env("CLAUDE_HUAWEI_ANTHROPIC_AUTH_TOKEN")
        or _env("CLAUDE_HUAWEI_API_KEY")
        or ""
    )


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

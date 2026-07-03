"""Agent provider 注册表。

核心 runtime 只依赖这里查找 provider。新增 backend 时注册一个实现即可，
不用再修改 send/poll/get_agent_card 的主分派逻辑。
"""

from __future__ import annotations

from maos_runtime.a2a_constants import (
    CLAUDE_BACKEND,
    CLAUDE_HUAWEI_BACKEND,
    CODEX_BACKEND,
    EVALUATOR_BACKEND,
    HERMES_BACKEND,
    MULTICA_BACKEND,
    SIMULATOR_BACKEND,
)
from maos_runtime.a2a_provider_base import ProviderRuntime


_BACKEND_ALIASES = {
    "agent-service": MULTICA_BACKEND,
    "agentservice": MULTICA_BACKEND,
    "real-agent": MULTICA_BACKEND,
    "multica-daemon": MULTICA_BACKEND,
    "sim": SIMULATOR_BACKEND,
    "mock": SIMULATOR_BACKEND,
    "simulator": SIMULATOR_BACKEND,
    "hermes": HERMES_BACKEND,
    "hermes-oneshot": HERMES_BACKEND,
    "direct-hermes": HERMES_BACKEND,
    "hermes-direct": HERMES_BACKEND,
    "codex": CODEX_BACKEND,
    "codex-cli": CODEX_BACKEND,
    "direct-codex": CODEX_BACKEND,
    "codex-direct": CODEX_BACKEND,
    "claude": CLAUDE_BACKEND,
    "claude-cli": CLAUDE_BACKEND,
    "direct-claude": CLAUDE_BACKEND,
    "claude-direct": CLAUDE_BACKEND,
    "claude-huawei": CLAUDE_HUAWEI_BACKEND,
    "claude-huawei-cli": CLAUDE_HUAWEI_BACKEND,
    "huawei-claude": CLAUDE_HUAWEI_BACKEND,
    "huawei-deepseek": CLAUDE_HUAWEI_BACKEND,
    "deepseek-huawei": CLAUDE_HUAWEI_BACKEND,
    "deepseek-v3.2": CLAUDE_HUAWEI_BACKEND,
    "evaluator": EVALUATOR_BACKEND,
    "evaluation": EVALUATOR_BACKEND,
    "deterministic-evaluator": EVALUATOR_BACKEND,
    "benchmark": EVALUATOR_BACKEND,
}

_PROVIDERS: dict[str, ProviderRuntime] = {}


def normalize_backend(backend: str) -> str:
    normalized = str(backend).lower().replace("_", "-")
    return _BACKEND_ALIASES.get(normalized, normalized)


def register_provider(
    provider: ProviderRuntime,
    *,
    aliases: list[str] | tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    backend = normalize_backend(provider.backend)
    if not backend:
        raise ValueError("Provider backend cannot be empty.")
    if backend in _PROVIDERS and not replace:
        raise ValueError(f"Provider backend already registered: {backend}")
    _PROVIDERS[backend] = provider
    for alias in aliases:
        _BACKEND_ALIASES[str(alias).lower().replace("_", "-")] = backend


def provider_for_backend(
    backend: str,
    *,
    node_id: str | None = None,
) -> ProviderRuntime:
    normalized = normalize_backend(backend)
    provider = _PROVIDERS.get(normalized)
    if provider:
        return provider
    node_hint = f" for node {node_id}" if node_id else ""
    known = ", ".join(list_provider_backends())
    raise ValueError(
        f"Unsupported agent backend{node_hint}: {backend!r}. Known backends: {known}."
    )


def list_provider_backends() -> list[str]:
    return sorted(_PROVIDERS)


def clear_provider_registry() -> None:
    """测试辅助：清空 provider 注册表。生产代码不应调用。"""

    _PROVIDERS.clear()

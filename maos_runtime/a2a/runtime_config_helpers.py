"""Backend and node configuration helpers for A2A runtime."""

import hashlib
import json
import os
import re
import uuid
from typing import Any

from maos_runtime.a2a_constants import (
    LIGHTWEIGHT_MULTICA_AGENT_KEY,
    LIGHTWEIGHT_RUNTIME_PROFILE,
    MULTICA_BACKEND,
    PROVIDED_CONTEXT_ONLY,
    SIMULATOR_BACKEND,
)
from maos_runtime.a2a_provider_base import ProviderRuntime
from maos_runtime.a2a_provider_registry import normalize_backend, provider_for_backend
from maos_runtime.runtime_config import agent_result_text_limit


def _stable_runtime_id(prefix: str, node_id: str, idempotency_key: str | None) -> str:
    if not idempotency_key:
        return f"{prefix}-{node_id}-{uuid.uuid4()}"
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    safe_node_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(node_id)).strip("-") or "node"
    return f"{prefix}-{safe_node_id}-{digest}"

def _node_agent_config(node: dict[str, Any]) -> dict[str, Any]:
    agent = node.get("agent", {})
    return agent if isinstance(agent, dict) else {}

def _node_result_text_limit(node: dict[str, Any]) -> int:
    agent = _node_agent_config(node)
    value = agent.get("result_text_limit", node.get("result_text_limit"))
    if value is None:
        return agent_result_text_limit()
    try:
        return int(value)
    except (TypeError, ValueError):
        return agent_result_text_limit()

def _metadata_result_text_limit(metadata: dict[str, Any]) -> int:
    value = metadata.get("resultTextLimit", metadata.get("result_text_limit"))
    if value is None:
        return agent_result_text_limit()
    try:
        return int(value)
    except (TypeError, ValueError):
        return agent_result_text_limit()

def _provider_for_node(node: dict[str, Any]) -> ProviderRuntime:
    return _provider_for_backend(_node_backend(node), node)

def _provider_for_backend(
    backend: str,
    node: dict[str, Any] | None = None,
) -> ProviderRuntime:
    node_id = node["id"] if node else None
    return provider_for_backend(backend, node_id=node_id)

def _node_backend(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    backend = (
        agent.get("backend")
        or node.get("backend")
        or node.get("runtime")
        or os.environ.get("DEFAULT_AGENT_BACKEND")
        or SIMULATOR_BACKEND
    )
    return normalize_backend(str(backend))

def _multica_context_policy(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = (
        agent.get("context_policy")
        or node.get("context_policy")
        or os.environ.get("DEFAULT_MULTICA_CONTEXT_POLICY")
        or PROVIDED_CONTEXT_ONLY
    )
    return str(value).lower().replace("-", "_")

def _multica_runtime_profile(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = (
        agent.get("runtime_profile")
        or agent.get("execution_profile")
        or node.get("runtime_profile")
        or os.environ.get("DEFAULT_MULTICA_RUNTIME_PROFILE")
    )
    if value:
        return str(value).lower().replace("-", "_")
    return "maos_compact_agent"

def _multica_execution_mode(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = agent.get("execution_mode") or node.get("execution_mode")
    if value:
        return str(value).lower().replace("-", "_")
    if _is_lightweight_multica_node(node):
        return "lightweight_hermes_oneshot"
    return "multica"

def _is_lightweight_multica_node(node: dict[str, Any]) -> bool:
    agent = _node_agent_config(node)
    execution_mode = str(agent.get("execution_mode") or node.get("execution_mode") or "").lower()
    execution_mode = execution_mode.replace("-", "_")
    runtime_profile = _multica_runtime_profile(node)
    if execution_mode in {"multica", "full_multica", "codex_cli"}:
        return False
    if execution_mode in {"lightweight", "lightweight_hermes_oneshot", "hermes", "hermes_oneshot"}:
        return True
    if runtime_profile in {"full", "full_multica", "codex", "codex_cli", "maos_compact", "maos_compact_agent"}:
        return False
    if runtime_profile in {"lightweight", "hermes", "hermes_oneshot", PROVIDED_CONTEXT_ONLY}:
        return True
    return False

def _multica_dispatch_agent_key(node: dict[str, Any]) -> str | None:
    agent = _node_agent_config(node)
    if not _is_lightweight_multica_node(node):
        return agent.get("agent_key") or node.get("agent_key")
    return (
        agent.get("dispatch_agent_key")
        or os.environ.get("LIGHTWEIGHT_MULTICA_AGENT_KEY")
        or LIGHTWEIGHT_MULTICA_AGENT_KEY
    )

def _multica_requested_agent_name(node: dict[str, Any]) -> str | None:
    agent = _node_agent_config(node)
    value = agent.get("agent_name") or node.get("agent_name")
    return None if value is None else str(value)

def _maos_compact_bootstrap_enabled(
    agent: dict[str, Any],
    node: dict[str, Any],
    runtime_profile: str,
) -> bool:
    value = agent.get("maos_compact_bootstrap", node.get("maos_compact_bootstrap"))
    if isinstance(value, bool):
        return value
    if value is not None:
        return str(value).lower() in {"1", "true", "yes", "on"}
    return runtime_profile in {"maos_compact", "maos_compact_agent"}

def _allowed_skill_slugs(agent: dict[str, Any], node: dict[str, Any]) -> str:
    value = agent.get("allowed_skill_slugs", node.get("allowed_skill_slugs", ""))
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value or "")

def _include_payload_metadata(node: dict[str, Any]) -> bool:
    agent = _node_agent_config(node)
    value = agent.get("include_payload_metadata", node.get("include_payload_metadata", False))
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}

def _payload_metadata_limit(node: dict[str, Any]) -> int:
    agent = _node_agent_config(node)
    value = agent.get("payload_metadata_limit", node.get("payload_metadata_limit"))
    if value is None:
        value = os.environ.get("MULTICA_PAYLOAD_METADATA_LIMIT", "2000")
    return int(value)

def _hermes_context_policy(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = (
        agent.get("context_policy")
        or node.get("context_policy")
        or os.environ.get("DEFAULT_HERMES_CONTEXT_POLICY")
        or PROVIDED_CONTEXT_ONLY
    )
    return str(value).lower().replace("-", "_")

def _hermes_runtime_profile(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = (
        agent.get("runtime_profile")
        or agent.get("execution_profile")
        or node.get("runtime_profile")
        or os.environ.get("DEFAULT_HERMES_RUNTIME_PROFILE")
        or "hermes_oneshot"
    )
    return str(value).lower().replace("-", "_")

def _hermes_execution_mode(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = agent.get("execution_mode") or node.get("execution_mode") or "hermes_oneshot"
    return str(value).lower().replace("-", "_")

def _hermes_prompt_limit(node: dict[str, Any]) -> int:
    if not _prompt_payload_truncation_enabled(node):
        return 0
    agent = _node_agent_config(node)
    value = agent.get("prompt_payload_limit", node.get("prompt_payload_limit"))
    if value is None:
        value = os.environ.get("HERMES_PROMPT_PAYLOAD_LIMIT", "12000")
    return int(value)

def _hermes_command_prompt_limit() -> int:
    return int(os.environ.get("HERMES_COMMAND_PROMPT_LIMIT", "26000"))

def _hermes_poll_seconds() -> float:
    return float(os.environ.get("HERMES_PROVIDER_POLL_SECONDS", "30"))

def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)

def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}

def _prompt_payload_truncation_enabled(node: dict[str, Any]) -> bool:
    agent = _node_agent_config(node)
    value = agent.get("truncate_prompt_payload", node.get("truncate_prompt_payload"))
    if value is None:
        value = os.environ.get("AGENT_PROMPT_PAYLOAD_TRUNCATION", "false")
    return _truthy(value)

def _dependency_artifact_transfer_mode(node: dict[str, Any]) -> str:
    agent = _node_agent_config(node)
    value = (
        agent.get("artifact_transfer_mode")
        or agent.get("dependency_artifact_mode")
        or node.get("artifact_transfer_mode")
        or node.get("dependency_artifact_mode")
        or os.environ.get("A2A_DEPENDENCY_ARTIFACT_MODE")
        or "ref"
    )
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in {"inline", "full", "full_inline", "embed", "embedded"}:
        return "inline"
    if normalized in {"ref", "refs", "reference", "external", "external_ref"}:
        return "ref"
    return "ref"

def _multica_description_limit(node: dict[str, Any]) -> int:
    if not _prompt_payload_truncation_enabled(node):
        return 0
    agent = _node_agent_config(node)
    value = agent.get("description_payload_limit", node.get("description_payload_limit"))
    if value is None:
        value = os.environ.get("MULTICA_DESCRIPTION_PAYLOAD_LIMIT", "60000")
    return int(value)

def _safe_json(value: Any, limit: int = 0) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if limit <= 0:
        return encoded
    if len(encoded) <= limit:
        return encoded
    return encoded[:limit] + "...<truncated>"

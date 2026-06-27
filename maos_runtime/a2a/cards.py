"""A2A AgentCard builders for built-in backends."""

from typing import Any

from maos_runtime.runtime_config import agent_service_api_base as _agent_service_api_base
from maos_runtime.a2a.runtime_config_helpers import _node_agent_config


def _simulator_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    operation = node.get("operation", "merge")
    return {
        "name": f"{node['id']}-simulator-agent",
        "description": (
            f"Simulator agent for control-flow node {node['id']}; dependencies are exchanged "
            "with A2A messages and artifacts"
        ),
        "supportedInterfaces": [
            {
                "transport": "JSONRPC",
                "url": "local://sandbox-agent-runtime/message:send",
            }
        ],
        "provider": {"organization": "MAOS Sandbox Runtime"},
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": operation,
                "name": operation,
                "description": f"Executes the {operation} control-flow operation",
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
        "protocolVersions": ["1.0"],
    }

def _multica_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    agent = _node_agent_config(node)
    agent_name = agent.get("agent_name") or agent.get("agent_key") or agent.get("agent_id") or "multica-agent"
    return {
        "name": str(agent_name),
        "description": f"Multica-backed real Agent Service for control-flow node {node['id']}",
        "supportedInterfaces": [
            {
                "transport": "HTTP+AgentService",
                "url": _agent_service_api_base(),
            }
        ],
        "provider": {"organization": "Multica"},
        "version": "0.1.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/markdown", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [
            {
                "id": str(agent.get("agent_key") or node.get("operation", "agent_task")),
                "name": str(agent_name),
                "description": "Executes a real Multica task and reports status through AgentService",
                "inputModes": ["text/markdown", "application/json"],
                "outputModes": ["text/markdown", "application/json"],
            }
        ],
        "protocolVersions": ["1.0-adapter"],
    }

def _hermes_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    agent = _node_agent_config(node)
    agent_name = agent.get("agent_name") or agent.get("agent_key") or "hermes-oneshot-agent"
    return {
        "name": str(agent_name),
        "description": f"Hermes one-shot Agent Service for control-flow node {node['id']}",
        "supportedInterfaces": [
            {
                "transport": "HTTP+AgentService",
                "url": _agent_service_api_base(),
            }
        ],
        "provider": {"organization": "Hermes Agent Runtime"},
        "version": "0.16.0-adapter",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/markdown", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [
            {
                "id": str(agent.get("agent_key") or node.get("operation", "agent_task")),
                "name": str(agent_name),
                "description": "Executes a Hermes one-shot task through the external Agent Service API",
                "inputModes": ["text/markdown", "application/json"],
                "outputModes": ["text/markdown", "application/json"],
            }
        ],
        "protocolVersions": ["1.0-adapter"],
    }

def _codex_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    agent = _node_agent_config(node)
    agent_name = agent.get("agent_name") or agent.get("agent_key") or "codex-cli-agent"
    return {
        "name": str(agent_name),
        "description": f"Codex CLI one-shot Agent runtime for control-flow node {node['id']}",
        "supportedInterfaces": [
            {
                "transport": "LocalProcess+CodexCLI",
                "url": "local://codex/exec",
            }
        ],
        "provider": {"organization": "OpenAI Codex CLI"},
        "version": "1.0.0-adapter",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/markdown", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [
            {
                "id": str(agent.get("agent_key") or node.get("operation", "agent_task")),
                "name": str(agent_name),
                "description": "Executes a one-shot task through the local Codex CLI",
                "inputModes": ["text/markdown", "application/json"],
                "outputModes": ["text/markdown", "application/json"],
            }
        ],
        "protocolVersions": ["1.0-adapter"],
    }

def _claude_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    agent = _node_agent_config(node)
    agent_name = agent.get("agent_name") or agent.get("agent_key") or "claude-cli-agent"
    return {
        "name": str(agent_name),
        "description": f"Claude CLI one-shot Agent runtime for control-flow node {node['id']}",
        "supportedInterfaces": [
            {
                "transport": "LocalProcess+ClaudeCLI",
                "url": "local://claude/print",
            }
        ],
        "provider": {"organization": "Anthropic Claude Code CLI"},
        "version": "1.0.0-adapter",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/markdown", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [
            {
                "id": str(agent.get("agent_key") or node.get("operation", "agent_task")),
                "name": str(agent_name),
                "description": "Executes a one-shot task through the local Claude CLI",
                "inputModes": ["text/markdown", "application/json"],
                "outputModes": ["text/markdown", "application/json"],
            }
        ],
        "protocolVersions": ["1.0-adapter"],
    }

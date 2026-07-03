"""Direct Claude CLI A2A provider implementation."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from maos_runtime.a2a.agent_service_client import _timestamp, _timestamp_from_epoch
from maos_runtime.a2a.messages import (
    _agent_message,
    _artifact_from_result,
    _dependency_results_from_artifacts,
    _event_from_a2a_task,
    _first_data_part,
)
from maos_runtime.a2a.claude_stream import (
    readable_claude_trace_from_stream,
    stream_json_events,
    stream_json_final_output,
    stream_json_terminal_event,
)
from maos_runtime.a2a.prompt_builders import _hermes_prompt
from maos_runtime.a2a.runtime_config_helpers import (
    _hermes_context_policy,
    _hermes_runtime_profile,
    _node_agent_config,
    _node_result_text_limit,
    _stable_runtime_id,
)
from maos_runtime.a2a.text_parsing import (
    _extract_structured_agent_output,
    _metadata_string,
    _truncate_text,
)
from maos_runtime.a2a_constants import CLAUDE_BACKEND, CLAUDE_HUAWEI_BACKEND
from maos_runtime.a2a_task_store import (
    TASKS as _TASKS,
    TASKS_LOCK as _TASKS_LOCK,
    request_idempotency_key as _request_idempotency_key,
    store_idempotent_task as _store_idempotent_task,
    store_task as _store_task,
)
from maos_runtime.runtime_config import (
    claude_allowed_tools,
    claude_bare_enabled,
    claude_cli_bin,
    claude_huawei_allowed_tools,
    claude_huawei_anthropic_base_url,
    claude_huawei_auth_token,
    claude_huawei_bare_enabled,
    claude_huawei_cli_bin,
    claude_huawei_model,
    claude_huawei_permission_mode,
    claude_huawei_timeout_seconds,
    claude_huawei_tools,
    claude_huawei_workdir,
    claude_model,
    claude_permission_mode,
    claude_timeout_seconds,
    claude_tools,
    claude_workdir,
)


_CLAUDE_PROCESSES: dict[str, subprocess.Popen[Any]] = {}


def _send_claude_message(
    request: dict[str, Any],
    *,
    backend: str = CLAUDE_BACKEND,
) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    if isinstance(payload.get("control_flow"), dict):
        node = {**node, "control_flow": payload["control_flow"]}
    agent = _node_agent_config(node)
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    graph_input = payload.get("graph_input", {})
    runtime_profile = _hermes_runtime_profile(node)
    context_policy = _hermes_context_policy(node)
    result_text_limit = _node_result_text_limit(node)
    dependency_results = _dependency_results_from_artifacts(
        payload.get("dependency_artifacts", []),
        resolve_refs=_resolve_dependency_artifact_refs(agent, node),
        expected_workflow_id=workflow_id,
    )
    prompt = _hermes_prompt(
        node,
        dependency_results,
        graph_input,
        workflow_id,
        task_id,
        context_policy,
        runtime_profile,
    )
    workdir = _claude_node_workdir(agent, node, backend)
    run_dir = _claude_run_dir(task_id, backend)
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_dir / "prompt.md"
    output_format = _claude_output_format(agent, node)
    stdout_file = run_dir / ("stdout.jsonl" if output_format == "stream-json" else "stdout.json")
    stderr_file = run_dir / "stderr.log"
    prompt_file.write_text(prompt, encoding="utf-8")

    command = _claude_command(agent, node, backend=backend)
    env = _claude_process_env(agent, node, backend)
    proc = _start_claude_process(command, prompt, workdir, stdout_file, stderr_file, env=env)
    _CLAUDE_PROCESSES[task_id] = proc
    now = _timestamp()
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_WORKING",
            "message": _agent_message(
                task_id,
                context_id,
                {
                    "status": "accepted",
                    "node_id": node["id"],
                    "runtime": backend,
                    "pid": proc.pid,
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": backend,
            "idempotencyKey": idempotency_key,
            "completionMode": "polling",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "agent_task"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "claudeTaskId": task_id,
            "claudePid": proc.pid,
            "claudeCommand": _display_command(command),
            "claudeWorkdir": str(workdir),
            "claudeRunDir": str(run_dir),
            "claudePromptFile": str(prompt_file),
            "claudeStdoutFile": str(stdout_file),
            "claudeStderrFile": str(stderr_file),
            "claudeOutputFormat": output_format,
            "claudeToolsDisabled": _claude_disable_tools(agent, node),
            "resolveDependencyArtifactRefs": _resolve_dependency_artifact_refs(agent, node),
            "claudePrompt": prompt,
            "agentKey": agent.get("agent_key") or node.get("agent_key") or backend,
            "requestedAgentKey": agent.get("agent_key") or node.get("agent_key") or backend,
            "contextPolicy": context_policy,
            "runtimeProfile": runtime_profile,
            "executionMode": _claude_execution_mode(backend),
            "resultTextLimit": result_text_limit,
            "strictStructuredOutput": bool(
                agent.get("strict_structured_output")
                or node.get("strict_structured_output")
            ),
            "requiredStructuredOutputFields": (
                agent.get("required_structured_output_fields")
                or node.get("required_structured_output_fields")
                or []
            ),
            "riskPassRequiresFinalVisit": bool(
                agent.get("risk_pass_requires_final_visit")
                or node.get("risk_pass_requires_final_visit")
            ),
            "controlFlowVisit": _control_flow_visit(node),
            "controlFlowMaxVisits": int(node.get("max_visits") or 1),
            "timeoutSeconds": _claude_timeout(agent, node, backend),
            "plannedDurationSeconds": None,
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": f"temporal-durable-polling-local-{backend}-cli",
            "transport": {
                "runtime": _claude_transport_name(backend),
                "command": "claude --print",
                "stdout": str(stdout_file),
                "stderr": str(stderr_file),
            },
        },
    }
    _store_task(task)
    return {"task": copy.deepcopy(task)}


def _poll_claude_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
    backend = _metadata_backend(metadata)
    started_at = float(metadata.get("startedAt", time.time()))
    elapsed = round(time.time() - started_at, 2)
    timeout = float(metadata.get("timeoutSeconds") or _claude_default_timeout_seconds(backend))
    proc = _CLAUDE_PROCESSES.get(task_id)
    return_code = proc.poll() if proc else _fallback_return_code(metadata)
    if return_code is None and elapsed > timeout:
        _terminate_claude_process(task_id, metadata)
        return _fail_claude_task(task_id, f"Claude CLI task timed out after {timeout} seconds", elapsed)

    now = _timestamp()
    with _TASKS_LOCK:
        metadata["heartbeatCount"] += 1
        metadata["elapsedSeconds"] = elapsed
        metadata["lastHeartbeatAt"] = now
        task["status"]["timestamp"] = now
        task["status"]["message"] = _agent_message(
            task_id,
            task["contextId"],
            {
                "status": "working" if return_code is None else "finished",
                "node_id": metadata["nodeId"],
                "runtime": backend,
                "pid": metadata.get("claudePid"),
                "return_code": return_code,
                "elapsed_seconds": elapsed,
            },
        )

    if return_code is None:
        with _TASKS_LOCK:
            current_task = copy.deepcopy(_TASKS[task_id]["task"])
        return {"done": False, "task": current_task, "event": None}
    _CLAUDE_PROCESSES.pop(task_id, None)
    if return_code != 0:
        stderr = _read_text(metadata.get("claudeStderrFile"))
        stdout = _read_text(metadata.get("claudeStdoutFile"))
        detail = stderr or stdout or f"Claude CLI exited with code {return_code}"
        return _fail_claude_task(task_id, detail, elapsed)
    completion_error = _claude_completion_error(metadata)
    if completion_error:
        return _fail_claude_task(task_id, completion_error, elapsed)
    output = _claude_output(metadata)
    if not output.strip():
        return _fail_claude_task(task_id, "Claude CLI completed without final output", elapsed)
    return _complete_claude_task(task_id, output, elapsed)


def _cancel_claude_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
    _terminate_claude_process(task_id, metadata)
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata["cancelReason"] = request.get("reason")
        metadata["cancelledAt"] = _timestamp()
        task["status"] = {
            "state": "TASK_STATE_CANCELED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "cancelled",
                    "node_id": metadata["nodeId"],
                    "reason": request.get("reason"),
                },
            ),
            "timestamp": _timestamp(),
        }
        cancelled = copy.deepcopy(task)
    _store_idempotent_task(cancelled)
    return {"ok": True, "task": cancelled}


def _complete_claude_task(task_id: str, output: str, elapsed: float) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        backend = _metadata_backend(metadata)
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["claudeReturnCode"] = 0
        if not record["result_artifact_created"]:
            result = _claude_result(metadata, output, elapsed)
            task["artifacts"] = [_artifact_from_result(result, metadata)]
            record["result_artifact_created"] = True
        task["status"] = {
            "state": "TASK_STATE_COMPLETED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "completed",
                    "node_id": metadata["nodeId"],
                    "runtime": backend,
                },
            ),
            "timestamp": _timestamp(),
        }
        completed_task = copy.deepcopy(task)
    _store_idempotent_task(completed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": _metadata_string(metadata, "workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "completed",
        },
        completed_task,
    )
    return {"done": True, "task": completed_task, "event": event}


def _fail_claude_task(task_id: str, error: str, elapsed: float) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        backend = _metadata_backend(metadata)
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["error"] = _truncate_text(error, 4000)
        task["status"] = {
            "state": "TASK_STATE_FAILED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "failed",
                    "node_id": metadata["nodeId"],
                    "runtime": backend,
                    "error": metadata["error"],
                },
            ),
            "timestamp": _timestamp(),
        }
        failed_task = copy.deepcopy(task)
    _store_idempotent_task(failed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": _metadata_string(metadata, "workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "failed",
            "error": metadata["error"],
        },
        failed_task,
    )
    return {"done": True, "task": failed_task, "event": event}


def _claude_result(metadata: dict[str, Any], output: str, elapsed: float) -> dict[str, Any]:
    # Keep the business output intact in the A2A result artifact. Temporal
    # history/API projections compact it later; downstream Agent nodes should
    # receive the complete upstream result.
    latest = output.strip()
    strict_structured_output = bool(metadata.get("strictStructuredOutput"))
    required_fields = metadata.get("requiredStructuredOutputFields") or []
    structured_output = _extract_structured_agent_output(
        output,
        allow_text_decision=not strict_structured_output,
        require_complete_decision=strict_structured_output,
        required_fields=required_fields if isinstance(required_fields, list) else [],
    )
    structured_output = _enforce_control_flow_decision_policy(structured_output, metadata)
    payload = {
        "status": "completed",
        "agent_backend": _metadata_backend(metadata),
        "agent_key": metadata.get("agentKey"),
        "requested_agent_key": metadata.get("requestedAgentKey"),
        "context_policy": metadata.get("contextPolicy"),
        "runtime_profile": metadata.get("runtimeProfile"),
        "execution_mode": metadata.get("executionMode"),
        "claude_task_id": metadata.get("claudeTaskId"),
        "claude_pid": metadata.get("claudePid"),
        "claude_command": metadata.get("claudeCommand"),
        "claude_workdir": metadata.get("claudeWorkdir"),
        "latest_comment": latest,
        "stdout": latest,
        "trace": _limited_trace_for_payload(_claude_trace(metadata, elapsed)),
    }
    if structured_output:
        payload["structured_output"] = structured_output
        for key, value in structured_output.items():
            payload.setdefault(key, value)
    return {
        "node": metadata["nodeId"],
        "operation": metadata["operation"],
        "duration_seconds": elapsed,
        "payload": payload,
    }


def _control_flow_visit(node: dict[str, Any]) -> int:
    control_flow = node.get("control_flow")
    if isinstance(control_flow, dict):
        try:
            return int(control_flow.get("visit") or 1)
        except (TypeError, ValueError):
            return 1
    return 1


def _enforce_control_flow_decision_policy(
    structured_output: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not structured_output:
        return structured_output
    if not metadata.get("riskPassRequiresFinalVisit"):
        return structured_output
    if structured_output.get("decision") != "approved_with_risk":
        return structured_output
    try:
        visit = int(metadata.get("controlFlowVisit") or 1)
        max_visits = int(metadata.get("controlFlowMaxVisits") or 1)
    except (TypeError, ValueError):
        return structured_output
    if visit >= max_visits:
        return structured_output
    adjusted = dict(structured_output)
    adjusted["original_decision"] = structured_output.get("decision")
    adjusted["decision"] = "needs_revision"
    adjusted["decision_adjustment_reason"] = (
        "approved_with_risk is only allowed on the final review visit; "
        "earlier risk-pass output is routed as needs_revision."
    )
    return adjusted


def _claude_command(
    agent: dict[str, Any],
    node: dict[str, Any],
    *,
    backend: str = CLAUDE_BACKEND,
) -> list[str]:
    command = [_resolve_claude_bin(agent, node, backend), "--print"]
    if _claude_bare(agent, node, backend):
        command.append("--bare")
    model = agent.get("model") or node.get("model") or _claude_default_model(backend)
    if model:
        command.extend(["--model", str(model)])
    permission_mode = (
        agent.get("permission_mode")
        or node.get("permission_mode")
        or _claude_default_permission_mode(backend)
    )
    if permission_mode:
        command.extend(["--permission-mode", str(permission_mode)])
    output_format = _claude_output_format(agent, node)
    command.extend(["--output-format", str(output_format)])
    tools = _claude_tools(agent, node, backend)
    if tools is not None:
        command.extend(["--tools", tools])
    allowed_tools = _claude_allowed_tools(agent, node, backend)
    if allowed_tools is not None:
        command.extend(["--allowedTools", allowed_tools])
    extra_args = agent.get("claude_args") or node.get("claude_args") or []
    if isinstance(extra_args, str):
        extra_args = [extra_args]
    if output_format == "stream-json" and "--verbose" not in [str(item) for item in extra_args]:
        command.append("--verbose")
    if output_format == "stream-json":
        command.extend(["--include-partial-messages", "--include-hook-events"])
    command.extend(str(item) for item in extra_args)
    return command


def _claude_output_format(agent: dict[str, Any], node: dict[str, Any]) -> str:
    value = agent.get("output_format") or node.get("output_format") or "stream-json"
    normalized = str(value).strip().lower()
    return normalized if normalized in {"json", "text", "stream-json"} else "stream-json"


def _start_claude_process(
    command: list[str],
    prompt: str,
    workdir: Path,
    stdout_file: Path,
    stderr_file: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[Any]:
    stdout_handle = stdout_file.open("w", encoding="utf-8", errors="replace")
    stderr_handle = stderr_file.open("w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
        return proc
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise


def _claude_node_workdir(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> Path:
    value = agent.get("workdir") or node.get("workdir") or _claude_default_workdir(backend)
    path = Path(str(value)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _claude_run_dir(task_id: str, backend: str = CLAUDE_BACKEND) -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return base / _claude_provider_dir(backend) / task_id


def _claude_timeout(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> float:
    value = agent.get("timeout_seconds") or node.get("timeout_seconds")
    return float(value) if value else _claude_default_timeout_seconds(backend)


def _resolve_claude_bin(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> str:
    value = agent.get("claude_bin") or node.get("claude_bin") or _claude_default_bin(backend)
    resolved = Path(shutil.which(str(value)) or str(value))
    if resolved.suffix.lower() in {".cmd", ".bat"}:
        native = (
            resolved.parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
        if native.exists():
            return str(native)
    return str(resolved)


def _display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command + ["<stdin>"])


def _metadata_backend(metadata: dict[str, Any]) -> str:
    backend = str(metadata.get("backend") or CLAUDE_BACKEND).strip().lower().replace("_", "-")
    return backend or CLAUDE_BACKEND


def _claude_provider_dir(backend: str) -> str:
    return "claude-huawei-provider" if backend == CLAUDE_HUAWEI_BACKEND else "claude-provider"


def _claude_execution_mode(backend: str) -> str:
    return "claude_huawei_print" if backend == CLAUDE_HUAWEI_BACKEND else "claude_print"


def _claude_transport_name(backend: str) -> str:
    if backend == CLAUDE_HUAWEI_BACKEND:
        return "local Claude CLI routed to Huawei Cloud DeepSeek"
    return "local Claude CLI"


def _claude_default_bin(backend: str) -> str:
    return claude_huawei_cli_bin() if backend == CLAUDE_HUAWEI_BACKEND else claude_cli_bin()


def _claude_default_model(backend: str) -> str:
    return claude_huawei_model() if backend == CLAUDE_HUAWEI_BACKEND else claude_model()


def _claude_default_workdir(backend: str) -> str:
    return claude_huawei_workdir() if backend == CLAUDE_HUAWEI_BACKEND else claude_workdir()


def _claude_default_timeout_seconds(backend: str) -> float:
    return (
        claude_huawei_timeout_seconds()
        if backend == CLAUDE_HUAWEI_BACKEND
        else claude_timeout_seconds()
    )


def _claude_default_permission_mode(backend: str) -> str:
    return (
        claude_huawei_permission_mode()
        if backend == CLAUDE_HUAWEI_BACKEND
        else claude_permission_mode()
    )


def _claude_default_bare_enabled(backend: str) -> bool:
    return claude_huawei_bare_enabled() if backend == CLAUDE_HUAWEI_BACKEND else claude_bare_enabled()


def _claude_default_tools(backend: str) -> str | None:
    return claude_huawei_tools() if backend == CLAUDE_HUAWEI_BACKEND else claude_tools()


def _claude_default_allowed_tools(backend: str) -> str | None:
    return (
        claude_huawei_allowed_tools()
        if backend == CLAUDE_HUAWEI_BACKEND
        else claude_allowed_tools()
    )


def _claude_process_env(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str,
) -> dict[str, str] | None:
    if backend != CLAUDE_HUAWEI_BACKEND:
        return None

    base_url = str(
        _first_config_value(agent, node, "anthropic_base_url", "base_url")
        or claude_huawei_anthropic_base_url()
    ).strip()
    token = _claude_huawei_token(agent, node)
    if not base_url:
        raise RuntimeError(
            "claude-huawei requires CLAUDE_HUAWEI_ANTHROPIC_BASE_URL to point at "
            "a Huawei Cloud Anthropic-compatible endpoint or a local Anthropic-to-Huawei proxy."
        )
    if not token:
        raise RuntimeError(
            "claude-huawei requires CLAUDE_HUAWEI_ANTHROPIC_AUTH_TOKEN or "
            "CLAUDE_HUAWEI_API_KEY. It will not fall back to the existing Aliyun Claude config."
        )

    env = os.environ.copy()
    config_dir = _claude_config_dir(backend)
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_file = config_dir / "settings.json"
    if not settings_file.exists():
        settings_file.write_text("{}", encoding="utf-8")
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")
    env["ANTHROPIC_AUTH_TOKEN"] = ""
    env["ANTHROPIC_API_KEY"] = token
    env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-api-key: {token}"
    env["CLAUDE_CLI_MODEL"] = _claude_default_model(backend)
    return env


def _claude_config_dir(backend: str) -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return base / f"{_claude_provider_dir(backend)}-config"


def _claude_huawei_token(agent: dict[str, Any], node: dict[str, Any]) -> str:
    token_env = _first_config_value(
        agent,
        node,
        "anthropic_auth_token_env",
        "api_key_env",
        "auth_token_env",
    )
    if token_env:
        return os.environ.get(str(token_env), "")
    return claude_huawei_auth_token()


def _claude_output(metadata: dict[str, Any]) -> str:
    stdout = _read_text(metadata.get("claudeStdoutFile"))
    if _is_stream_json_stdout(metadata):
        result = _stream_json_final_output(stdout)
        return result if result is not None else stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    result = payload.get("result")
    if isinstance(result, str):
        return result
    return stdout


def _claude_completion_error(metadata: dict[str, Any]) -> str | None:
    stdout = _read_text(metadata.get("claudeStdoutFile"))
    if not stdout.strip():
        return "Claude CLI completed without stdout"
    if _is_stream_json_stdout(metadata):
        terminal = _stream_json_terminal_event(stdout)
        if terminal is None:
            return "Claude CLI stream-json output did not contain a terminal result event"
        if terminal.get("is_error") is True or terminal.get("subtype") == "error":
            return _truncate_text(json.dumps(terminal, ensure_ascii=False), 4000)
        terminal_reason = terminal.get("terminal_reason")
        if terminal_reason and terminal_reason not in {"completed"}:
            return _truncate_text(
                f"Claude CLI terminal_reason={terminal_reason}: "
                f"{json.dumps(terminal, ensure_ascii=False)}",
                4000,
            )
        result = terminal.get("result")
        if isinstance(result, str) and result.strip():
            return None
        return _truncate_text(
            f"Claude CLI stream-json result event did not contain a non-empty result: "
            f"{json.dumps(terminal, ensure_ascii=False)}",
            4000,
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if payload.get("is_error") is True or payload.get("subtype") == "error":
        return _truncate_text(stdout, 4000)
    terminal_reason = payload.get("terminal_reason")
    if terminal_reason and terminal_reason not in {"completed"}:
        return _truncate_text(f"Claude CLI terminal_reason={terminal_reason}: {stdout}", 4000)
    if isinstance(payload.get("result"), str) and payload["result"].strip():
        return None
    return _truncate_text(f"Claude CLI JSON output did not contain a non-empty result: {stdout}", 4000)


def _claude_bare(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> bool:
    value = agent.get("bare")
    if value is None:
        value = node.get("bare")
    if value is None:
        return _claude_default_bare_enabled(backend)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _claude_tools(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> str | None:
    if _claude_disable_tools(agent, node):
        return None
    value = agent.get("tools")
    if value is None:
        value = node.get("tools")
    if value is None:
        value = _claude_default_tools(backend)
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value) if value else None
    return str(value)


def _claude_allowed_tools(
    agent: dict[str, Any],
    node: dict[str, Any],
    backend: str = CLAUDE_BACKEND,
) -> str | None:
    if _claude_disable_tools(agent, node):
        return None
    value = agent.get("allowed_tools")
    if value is None:
        value = agent.get("allowedTools")
    if value is None:
        value = node.get("allowed_tools")
    if value is None:
        value = node.get("allowedTools")
    if value is None:
        value = _claude_default_allowed_tools(backend)
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value) if value else None
    return str(value)


def _claude_disable_tools(agent: dict[str, Any], node: dict[str, Any]) -> bool:
    value = _first_config_value(
        agent,
        node,
        "disable_tools",
        "disableTools",
        "tools_disabled",
        "toolsDisabled",
    )
    if value is None:
        tools_enabled = _first_config_value(agent, node, "tools_enabled", "toolsEnabled")
        if tools_enabled is not None:
            return not _truthy_config_value(tools_enabled)
        return False
    return _truthy_config_value(value)


def _resolve_dependency_artifact_refs(agent: dict[str, Any], node: dict[str, Any]) -> bool:
    value = _first_config_value(
        agent,
        node,
        "resolve_artifacts_before_prompt",
        "resolveArtifactsBeforePrompt",
        "resolve_dependency_artifacts",
        "resolveDependencyArtifacts",
    )
    if value is not None:
        return _truthy_config_value(value)
    dependency_resolution = _first_config_value(
        agent,
        node,
        "dependency_resolution",
        "dependencyResolution",
    )
    if dependency_resolution is not None:
        normalized = str(dependency_resolution).strip().lower()
        return normalized in {"inline", "resolve", "resolved", "resolve_refs", "full"}

    # If Claude cannot use WebFetch/Bash, provider-side resolution is the only
    # way for ref-mode upstream artifacts to reach the runtime as full context.
    return _claude_disable_tools(agent, node)


def _first_config_value(agent: dict[str, Any], node: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in agent:
            return agent.get(key)
        if key in node:
            return node.get(key)
    return None


def _truthy_config_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _read_text(path: Any) -> str:
    if not path:
        return ""
    try:
        return Path(str(path)).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _fallback_return_code(metadata: dict[str, Any]) -> int | None:
    stdout_file = metadata.get("claudeStdoutFile")
    if stdout_file and Path(str(stdout_file)).exists() and _claude_process_output_is_complete(metadata):
        return 0
    pid = metadata.get("claudePid")
    if pid and _pid_running(int(pid)):
        return None
    return 1


def _claude_process_output_is_complete(metadata: dict[str, Any]) -> bool:
    stdout = _read_text(metadata.get("claudeStdoutFile"))
    if not stdout.strip():
        return False
    if _is_stream_json_stdout(metadata):
        return _stream_json_terminal_event(stdout) is not None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return True
    return bool(payload.get("terminal_reason") or payload.get("type") == "result")


def _is_stream_json_stdout(metadata: dict[str, Any]) -> bool:
    if str(metadata.get("claudeOutputFormat") or "").lower() == "stream-json":
        return True
    stdout_file = str(metadata.get("claudeStdoutFile") or "")
    return stdout_file.lower().endswith(".jsonl")


def _stream_json_events(stdout: str) -> list[dict[str, Any]]:
    return stream_json_events(stdout)


def _stream_json_terminal_event(stdout: str) -> dict[str, Any] | None:
    return stream_json_terminal_event(stdout)


def _stream_json_final_output(stdout: str) -> str | None:
    return stream_json_final_output(stdout)


def _claude_trace(metadata: dict[str, Any], elapsed: float) -> list[dict[str, Any]]:
    return readable_claude_trace_from_stream(
        _read_text(metadata.get("claudeStdoutFile")),
        started_at=_timestamp_from_epoch(metadata.get("startedAt")),
        finished_at=_timestamp(),
        elapsed=elapsed,
        include_lifecycle=True,
    )


def _limited_trace_for_payload(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(trace) <= 80:
        return trace
    return [
        trace[0],
        {
            "type": "claude.trace_compacted",
            "title": "Claude trace compacted for workflow payload",
            "description": f"{len(trace) - 80} middle stream events are available from stdout.jsonl in the Web UI.",
        },
        *trace[-79:],
    ]


def _trace_events_from_stream_event(event: dict[str, Any], index: int) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "event")
    timestamp = event.get("timestamp") or event.get("created_at")
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else event.get("content")
    if isinstance(content, list):
        steps: list[dict[str, Any]] = []
        for part_index, part in enumerate(content, start=1):
            if not isinstance(part, dict):
                continue
            steps.append(_trace_event_from_content_part(part, event_type, timestamp, index, part_index))
        if steps:
            return steps
    return [
        {
            "type": f"claude.{event_type}",
            "title": _stream_event_title(event),
            "description": _stream_event_description(event),
            "timestamp": timestamp,
            "preview": _truncate_text(json.dumps(event, ensure_ascii=False), 2000),
        }
    ]


def _trace_event_from_content_part(
    part: dict[str, Any],
    event_type: str,
    timestamp: Any,
    index: int,
    part_index: int,
) -> dict[str, Any]:
    part_type = str(part.get("type") or event_type)
    tool_name = part.get("name") or part.get("tool_name")
    if part_type == "tool_use":
        title = f"Claude requested tool: {tool_name or 'tool'}"
        description = "Claude CLI emitted a tool call request."
    elif part_type == "tool_result":
        title = f"Tool result: {tool_name or part.get('tool_use_id') or 'tool'}"
        description = "Claude CLI received a tool execution result."
    elif part_type in {"text", "thinking"}:
        title = "Claude generated assistant text"
        description = "Claude CLI emitted visible assistant output."
    else:
        title = f"Claude stream content: {part_type}"
        description = "Claude CLI emitted a stream-json content block."
    return {
        "type": f"claude.{part_type}",
        "title": title,
        "description": description,
        "timestamp": timestamp,
        "tool": tool_name,
        "stream_index": index,
        "part_index": part_index,
        "preview": _truncate_text(json.dumps(part, ensure_ascii=False), 2000),
    }


def _stream_event_title(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if event_type == "system":
        return "Claude session initialized"
    if event_type == "assistant":
        return "Claude assistant message"
    if event_type == "user":
        return "Claude user/tool-result message"
    if event_type == "result":
        return "Claude terminal result"
    if "hook" in event_type:
        return "Claude hook event"
    return f"Claude stream event: {event_type}"


def _stream_event_description(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if event_type == "result":
        return "The Claude CLI run completed and reported final usage/result metadata."
    if event_type == "system":
        return "The Claude CLI runtime emitted session metadata."
    if event_type in {"assistant", "user"}:
        return "The Claude CLI runtime emitted a conversation message."
    return "The Claude CLI runtime emitted a stream-json event."


def _pid_running(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_claude_process(task_id: str, metadata: dict[str, Any]) -> None:
    proc = _CLAUDE_PROCESSES.pop(task_id, None)
    pid = int(metadata.get("claudePid") or 0)
    if proc and proc.poll() is None:
        proc.terminate()
    if pid and _pid_running(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass

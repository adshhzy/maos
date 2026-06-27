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
from maos_runtime.a2a_constants import CLAUDE_BACKEND
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
    claude_model,
    claude_permission_mode,
    claude_timeout_seconds,
    claude_tools,
    claude_workdir,
)


_CLAUDE_PROCESSES: dict[str, subprocess.Popen[Any]] = {}


def _send_claude_message(request: dict[str, Any]) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    agent = _node_agent_config(node)
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    dependency_results = _dependency_results_from_artifacts(payload.get("dependency_artifacts", []))
    graph_input = payload.get("graph_input", {})
    runtime_profile = _hermes_runtime_profile(node)
    context_policy = _hermes_context_policy(node)
    result_text_limit = _node_result_text_limit(node)
    prompt = _hermes_prompt(
        node,
        dependency_results,
        graph_input,
        workflow_id,
        task_id,
        context_policy,
        runtime_profile,
    )
    workdir = _claude_node_workdir(agent, node)
    run_dir = _claude_run_dir(task_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_dir / "prompt.md"
    stdout_file = run_dir / "stdout.json"
    stderr_file = run_dir / "stderr.log"
    prompt_file.write_text(prompt, encoding="utf-8")

    command = _claude_command(agent, node)
    proc = _start_claude_process(command, prompt, workdir, stdout_file, stderr_file)
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
                    "runtime": CLAUDE_BACKEND,
                    "pid": proc.pid,
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": CLAUDE_BACKEND,
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
            "claudePrompt": prompt,
            "agentKey": agent.get("agent_key") or node.get("agent_key") or "claude",
            "requestedAgentKey": agent.get("agent_key") or node.get("agent_key") or "claude",
            "contextPolicy": context_policy,
            "runtimeProfile": runtime_profile,
            "executionMode": "claude_print",
            "resultTextLimit": result_text_limit,
            "timeoutSeconds": _claude_timeout(agent, node),
            "plannedDurationSeconds": None,
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": "temporal-durable-polling-local-claude-cli",
            "transport": {
                "runtime": "local Claude CLI",
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
    started_at = float(metadata.get("startedAt", time.time()))
    elapsed = round(time.time() - started_at, 2)
    timeout = float(metadata.get("timeoutSeconds") or claude_timeout_seconds())
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
                "runtime": CLAUDE_BACKEND,
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
                    "runtime": CLAUDE_BACKEND,
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
                    "runtime": CLAUDE_BACKEND,
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
    structured_output = _extract_structured_agent_output(output)
    payload = {
        "status": "completed",
        "agent_backend": CLAUDE_BACKEND,
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
        "trace": [
            {
                "type": "claude.submit",
                "title": "Started Claude CLI one-shot task",
                "timestamp": _timestamp_from_epoch(metadata.get("startedAt")),
            },
            {
                "type": "claude.complete",
                "title": "Claude CLI returned final output",
                "timestamp": _timestamp(),
                "duration_seconds": elapsed,
            },
        ],
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


def _claude_command(agent: dict[str, Any], node: dict[str, Any]) -> list[str]:
    command = [_resolve_claude_bin(agent, node), "--print"]
    if _claude_bare(agent, node):
        command.append("--bare")
    model = agent.get("model") or node.get("model") or claude_model()
    if model:
        command.extend(["--model", str(model)])
    permission_mode = agent.get("permission_mode") or node.get("permission_mode") or claude_permission_mode()
    if permission_mode:
        command.extend(["--permission-mode", str(permission_mode)])
    output_format = agent.get("output_format") or node.get("output_format") or "json"
    command.extend(["--output-format", str(output_format)])
    tools = _claude_tools(agent, node)
    if tools is not None:
        command.extend(["--tools", tools])
    allowed_tools = _claude_allowed_tools(agent, node)
    if allowed_tools is not None:
        command.extend(["--allowedTools", allowed_tools])
    extra_args = agent.get("claude_args") or node.get("claude_args") or []
    if isinstance(extra_args, str):
        extra_args = [extra_args]
    command.extend(str(item) for item in extra_args)
    return command


def _start_claude_process(
    command: list[str],
    prompt: str,
    workdir: Path,
    stdout_file: Path,
    stderr_file: Path,
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


def _claude_node_workdir(agent: dict[str, Any], node: dict[str, Any]) -> Path:
    value = agent.get("workdir") or node.get("workdir") or claude_workdir()
    path = Path(str(value)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _claude_run_dir(task_id: str) -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return base / "claude-provider" / task_id


def _claude_timeout(agent: dict[str, Any], node: dict[str, Any]) -> float:
    value = agent.get("timeout_seconds") or node.get("timeout_seconds")
    return float(value) if value else claude_timeout_seconds()


def _resolve_claude_bin(agent: dict[str, Any], node: dict[str, Any]) -> str:
    value = agent.get("claude_bin") or node.get("claude_bin") or claude_cli_bin()
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


def _claude_output(metadata: dict[str, Any]) -> str:
    stdout = _read_text(metadata.get("claudeStdoutFile"))
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


def _claude_bare(agent: dict[str, Any], node: dict[str, Any]) -> bool:
    value = agent.get("bare")
    if value is None:
        value = node.get("bare")
    if value is None:
        return claude_bare_enabled()
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _claude_tools(agent: dict[str, Any], node: dict[str, Any]) -> str | None:
    value = agent.get("tools")
    if value is None:
        value = node.get("tools")
    if value is None:
        value = claude_tools()
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _claude_allowed_tools(agent: dict[str, Any], node: dict[str, Any]) -> str | None:
    value = agent.get("allowed_tools")
    if value is None:
        value = agent.get("allowedTools")
    if value is None:
        value = node.get("allowed_tools")
    if value is None:
        value = node.get("allowedTools")
    if value is None:
        value = claude_allowed_tools()
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


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
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return True
    return bool(payload.get("terminal_reason") or payload.get("type") == "result")


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

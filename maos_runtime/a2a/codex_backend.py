"""Direct Codex CLI A2A provider implementation."""

from __future__ import annotations

import copy
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
from maos_runtime.a2a.codex_pool import ensure_codex_runtime_pool_started
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
from maos_runtime.a2a_constants import CODEX_BACKEND
from maos_runtime.a2a_task_store import (
    TASKS as _TASKS,
    TASKS_LOCK as _TASKS_LOCK,
    request_idempotency_key as _request_idempotency_key,
    store_idempotent_task as _store_idempotent_task,
    store_task as _store_task,
)
from maos_runtime.runtime_config import (
    codex_cli_bin,
    codex_sandbox_mode,
    codex_timeout_seconds,
    codex_workdir,
)


_CODEX_PROCESSES: dict[str, subprocess.Popen[Any]] = {}


def _send_codex_message(request: dict[str, Any]) -> dict[str, Any]:
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
    pool_status = ensure_codex_runtime_pool_started()
    prompt = _hermes_prompt(
        node,
        dependency_results,
        graph_input,
        workflow_id,
        task_id,
        context_policy,
        runtime_profile,
    )
    workdir = _codex_node_workdir(agent, node)
    run_dir = _codex_run_dir(task_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_dir / "prompt.md"
    stdout_file = run_dir / "stdout.jsonl"
    stderr_file = run_dir / "stderr.log"
    output_file = run_dir / "final_output.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    command = _codex_command(agent, node, workdir, output_file)
    proc = _start_codex_process(command, prompt, workdir, stdout_file, stderr_file)
    _CODEX_PROCESSES[task_id] = proc
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
                    "runtime": CODEX_BACKEND,
                    "pid": proc.pid,
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": CODEX_BACKEND,
            "idempotencyKey": idempotency_key,
            "completionMode": "polling",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "agent_task"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "codexTaskId": task_id,
            "codexPid": proc.pid,
            "codexCommand": _display_command(command),
            "codexWorkdir": str(workdir),
            "codexRunDir": str(run_dir),
            "codexPromptFile": str(prompt_file),
            "codexStdoutFile": str(stdout_file),
            "codexStderrFile": str(stderr_file),
            "codexOutputFile": str(output_file),
            "codexPrompt": prompt,
            "agentKey": agent.get("agent_key") or node.get("agent_key") or "codex",
            "requestedAgentKey": agent.get("agent_key") or node.get("agent_key") or "codex",
            "contextPolicy": context_policy,
            "runtimeProfile": runtime_profile,
            "executionMode": "codex_exec",
            "runtimePool": pool_status,
            "resultTextLimit": result_text_limit,
            "timeoutSeconds": _codex_timeout(agent, node),
            "plannedDurationSeconds": None,
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": "temporal-durable-polling-local-codex-cli",
            "transport": {
                "runtime": "local Codex CLI",
                "command": "codex exec",
                "stdout": str(stdout_file),
                "stderr": str(stderr_file),
                "output": str(output_file),
            },
        },
    }
    _store_task(task)
    return {"task": copy.deepcopy(task)}


def _poll_codex_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
    started_at = float(metadata.get("startedAt", time.time()))
    elapsed = round(time.time() - started_at, 2)
    timeout = float(metadata.get("timeoutSeconds") or codex_timeout_seconds())
    proc = _CODEX_PROCESSES.get(task_id)
    return_code = proc.poll() if proc else _fallback_return_code(metadata)
    if return_code is None and elapsed > timeout:
        _terminate_codex_process(task_id, metadata)
        return _fail_codex_task(task_id, f"Codex CLI task timed out after {timeout} seconds", elapsed)

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
                "runtime": CODEX_BACKEND,
                "pid": metadata.get("codexPid"),
                "return_code": return_code,
                "elapsed_seconds": elapsed,
            },
        )

    if return_code is None:
        with _TASKS_LOCK:
            current_task = copy.deepcopy(_TASKS[task_id]["task"])
        return {"done": False, "task": current_task, "event": None}
    _CODEX_PROCESSES.pop(task_id, None)
    if return_code != 0:
        stderr = _read_text(metadata.get("codexStderrFile"))
        stdout = _read_text(metadata.get("codexStdoutFile"))
        detail = stderr or stdout or f"Codex CLI exited with code {return_code}"
        return _fail_codex_task(task_id, detail, elapsed)
    output = _codex_output(metadata)
    if not output.strip():
        return _fail_codex_task(task_id, "Codex CLI completed without final output", elapsed)
    return _complete_codex_task(task_id, output, elapsed)


def _cancel_codex_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
    _terminate_codex_process(task_id, metadata)
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


def _complete_codex_task(task_id: str, output: str, elapsed: float) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["codexReturnCode"] = 0
        if not record["result_artifact_created"]:
            result = _codex_result(metadata, output, elapsed)
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
                    "runtime": CODEX_BACKEND,
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


def _fail_codex_task(task_id: str, error: str, elapsed: float) -> dict[str, Any]:
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
                    "runtime": CODEX_BACKEND,
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


def _codex_result(metadata: dict[str, Any], output: str, elapsed: float) -> dict[str, Any]:
    # Keep the business output intact in the A2A result artifact. Temporal
    # history/API projections compact it later; downstream Agent nodes should
    # receive the complete upstream result.
    latest = output.strip()
    structured_output = _extract_structured_agent_output(output)
    payload = {
        "status": "completed",
        "agent_backend": CODEX_BACKEND,
        "agent_key": metadata.get("agentKey"),
        "requested_agent_key": metadata.get("requestedAgentKey"),
        "context_policy": metadata.get("contextPolicy"),
        "runtime_profile": metadata.get("runtimeProfile"),
        "execution_mode": metadata.get("executionMode"),
        "codex_task_id": metadata.get("codexTaskId"),
        "codex_pid": metadata.get("codexPid"),
        "codex_command": metadata.get("codexCommand"),
        "codex_workdir": metadata.get("codexWorkdir"),
        "runtime_pool": metadata.get("runtimePool"),
        "latest_comment": latest,
        "stdout": latest,
        "trace": [
            {
                "type": "codex.submit",
                "title": "Started Codex CLI one-shot task",
                "timestamp": _timestamp_from_epoch(metadata.get("startedAt")),
            },
            {
                "type": "codex.complete",
                "title": "Codex CLI returned final output",
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


def _codex_command(
    agent: dict[str, Any],
    node: dict[str, Any],
    workdir: Path,
    output_file: Path,
) -> list[str]:
    command = [_resolve_codex_bin(agent, node)]
    approval_policy = agent.get("approval_policy") or node.get("approval_policy")
    if approval_policy:
        # Codex CLI treats approval as a top-level option in current desktop builds.
        command.extend(["-a", str(approval_policy)])
    command.extend([
        "exec",
        "--json",
        "--output-last-message",
        str(output_file),
        "--cd",
        str(workdir),
        "--sandbox",
        str(agent.get("sandbox") or node.get("sandbox") or codex_sandbox_mode()),
        "--skip-git-repo-check",
    ])
    model = agent.get("model") or node.get("model")
    if model:
        command.extend(["--model", str(model)])
    profile = agent.get("profile") or node.get("profile")
    if profile:
        command.extend(["--profile", str(profile)])
    extra_args = agent.get("codex_args") or node.get("codex_args") or []
    if isinstance(extra_args, str):
        extra_args = [extra_args]
    command.extend(str(item) for item in extra_args)
    command.append("-")
    return command


def _start_codex_process(
    command: list[str],
    prompt: str,
    workdir: Path,
    stdout_file: Path,
    stderr_file: Path,
) -> subprocess.Popen[Any]:
    stdout_handle = stdout_file.open("w", encoding="utf-8", errors="replace")
    stderr_handle = stderr_file.open("w", encoding="utf-8", errors="replace")
    shell = command[0].lower().endswith((".cmd", ".bat"))
    popen_command: str | list[str] = subprocess.list2cmdline(command) if shell else command
    try:
        proc = subprocess.Popen(
            popen_command,
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=shell,
        )
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
        return proc
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise


def _codex_node_workdir(agent: dict[str, Any], node: dict[str, Any]) -> Path:
    value = agent.get("workdir") or node.get("workdir") or codex_workdir()
    path = Path(str(value)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_run_dir(task_id: str) -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return base / "codex-provider" / task_id


def _codex_timeout(agent: dict[str, Any], node: dict[str, Any]) -> float:
    value = agent.get("timeout_seconds") or node.get("timeout_seconds")
    return float(value) if value else codex_timeout_seconds()


def _resolve_codex_bin(agent: dict[str, Any], node: dict[str, Any]) -> str:
    value = agent.get("codex_bin") or node.get("codex_bin") or codex_cli_bin()
    resolved = shutil.which(str(value)) or str(value)
    return resolved


def _display_command(command: list[str]) -> str:
    display = list(command)
    if display and display[-1] == "-":
        display[-1] = "<stdin>"
    return subprocess.list2cmdline(display)


def _codex_output(metadata: dict[str, Any]) -> str:
    output = _read_text(metadata.get("codexOutputFile"))
    if output.strip():
        return output
    stdout = _read_text(metadata.get("codexStdoutFile"))
    return _last_json_event_message(stdout) or stdout


def _last_json_event_message(stdout: str) -> str:
    # Codex --json prints JSONL events. The last message file is authoritative,
    # but this fallback keeps older CLI versions usable.
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if '"message"' in line or '"content"' in line or '"text"' in line:
            return line
    return ""


def _read_text(path: Any) -> str:
    if not path:
        return ""
    try:
        return Path(str(path)).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _fallback_return_code(metadata: dict[str, Any]) -> int | None:
    output_file = metadata.get("codexOutputFile")
    if output_file and Path(str(output_file)).exists() and _read_text(output_file).strip():
        return 0
    pid = metadata.get("codexPid")
    if pid and _pid_running(int(pid)):
        return None
    return 1


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


def _terminate_codex_process(task_id: str, metadata: dict[str, Any]) -> None:
    proc = _CODEX_PROCESSES.pop(task_id, None)
    pid = int(metadata.get("codexPid") or 0)
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

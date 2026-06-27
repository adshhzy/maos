"""AgentService query and trace conversion facade for the Web UI."""

import json
import os
import re
from pathlib import Path
from typing import Any

from web.agent.client import agent_service_base, agent_service_get
from web.agent.input_projection import extract_agent_input
from web.agent.normalization import (
    compact_run,
    compact_task,
    response_items,
    select_latest_run,
    unwrap_data,
)
from web.agent.output_projection import select_final_output
from web.agent.timeline import build_trace_timeline, summarize_trace
from web.agent.trace_steps import build_trace_steps


def build_agent_trace(task_id: str) -> dict[str, Any]:
    task_response = agent_service_get(f"/tasks/{task_id}")
    runs_response = agent_service_get(f"/tasks/{task_id}/runs")
    comments_response = agent_service_get(f"/tasks/{task_id}/comments", {"recent": 20})
    task = unwrap_data(task_response)
    runs = response_items(runs_response)
    comments = response_items(comments_response)
    latest_run = select_latest_run(runs)
    messages: list[dict[str, Any]] = []
    if latest_run.get("id"):
        messages_response = agent_service_get(
            f"/runs/{latest_run['id']}/messages",
            {"issue_id": task_id},
        )
        messages = response_items(messages_response)
    steps = build_trace_steps(messages)
    return {
        "ok": True,
        "agent_service": agent_service_base(),
        "task": compact_task(task),
        "agent_input": extract_agent_input(task),
        "run": compact_run(latest_run),
        "final_output": select_final_output(comments),
        "summary": summarize_trace(steps),
        "timeline": build_trace_timeline(steps),
        "steps": steps,
    }


def build_agent_input(task_id: str) -> dict[str, Any]:
    task_response = agent_service_get(f"/tasks/{task_id}")
    task = unwrap_data(task_response)
    return {
        "ok": True,
        "agent_service": agent_service_base(),
        "task": compact_task(task),
        "agent_input": extract_agent_input(task),
    }


def build_local_runtime_input(task_id: str, backend: str) -> dict[str, Any]:
    normalized = str(backend or "").strip().lower().replace("_", "-")
    run_dir = _local_runtime_run_dir(task_id, normalized)
    prompt_file = (run_dir / "prompt.md").resolve()
    if not prompt_file.is_file():
        raise FileNotFoundError(f"Local runtime prompt not found: {task_id}")

    prompt = prompt_file.read_text(encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "backend": normalized,
        "task_id": task_id,
        "prompt_file": str(prompt_file),
        "prompt_chars": len(prompt),
        "agent_input": extract_agent_input({"description": prompt}),
    }


def build_local_runtime_output(task_id: str, backend: str) -> dict[str, Any]:
    normalized = str(backend or "").strip().lower().replace("_", "-")
    run_dir = _local_runtime_run_dir(task_id, normalized)
    if normalized in {"claude", "claude-cli"}:
        output_file = (run_dir / "stdout.json").resolve()
        output = _claude_runtime_output(output_file)
    elif normalized in {"codex", "codex-cli"}:
        output_file = (run_dir / "final_output.md").resolve()
        output = output_file.read_text(encoding="utf-8", errors="replace") if output_file.is_file() else ""
        if not output.strip():
            output_file = (run_dir / "stdout.jsonl").resolve()
            output = _codex_runtime_output(output_file)
    else:
        raise ValueError(f"Unsupported local runtime backend: {backend}")
    if not output_file.is_file():
        raise FileNotFoundError(f"Local runtime output not found: {task_id}")

    output = output.strip()
    return {
        "ok": True,
        "backend": normalized,
        "task_id": task_id,
        "output_file": str(output_file),
        "output_chars": len(output),
        "final_output": {
            "id": task_id,
            "content": output,
            "chars": len(output),
            "approx_tokens": max(1, (len(output) + 3) // 4) if output else 0,
        },
    }


def _local_runtime_run_dir(task_id: str, backend: str) -> Path:
    provider_dirs = {
        "claude": "claude-provider",
        "claude-cli": "claude-provider",
        "codex": "codex-provider",
        "codex-cli": "codex-provider",
    }
    provider_dir = provider_dirs.get(backend)
    if not provider_dir:
        raise ValueError(f"Unsupported local runtime backend: {backend}")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", task_id):
        raise ValueError("Invalid local runtime task id")

    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data")).resolve()
    provider_root = (base / provider_dir).resolve()
    run_dir = (provider_root / task_id).resolve()
    if provider_root not in run_dir.parents:
        raise ValueError("Invalid local runtime task path")
    return run_dir


def _claude_runtime_output(stdout_file: Path) -> str:
    stdout = stdout_file.read_text(encoding="utf-8", errors="replace") if stdout_file.is_file() else ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, str) else stdout


def _codex_runtime_output(stdout_file: Path) -> str:
    stdout = stdout_file.read_text(encoding="utf-8", errors="replace") if stdout_file.is_file() else ""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if '"message"' in line or '"content"' in line or '"text"' in line:
            return line
    return stdout

"""AgentService query and trace conversion facade for the Web UI."""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from maos_runtime.a2a.claude_stream import (
    readable_claude_partial_output,
    readable_claude_trace_from_stream,
    stream_json_events,
    stream_json_final_output,
)
from maos_runtime.persistence import execution_store_db_file, load_evaluation_report
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
    if normalized == "evaluator":
        report = _evaluator_runtime_report(task_id)
        config = report.get("config") or report.get("evaluation_config") or {}
        prompt = json.dumps(config, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "backend": normalized,
            "task_id": task_id,
            "prompt_file": _provider_task_store_file(),
            "prompt_chars": len(prompt),
            "agent_input": extract_agent_input({"description": prompt}),
        }
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
    report: dict[str, Any] | None = None
    trace: list[dict[str, Any]] | None = None
    if normalized in {"claude", "claude-cli", "claude-huawei", "claude-huawei-cli"}:
        run_dir = _local_runtime_run_dir(task_id, normalized)
        output_file = _claude_runtime_output_file(run_dir)
        output = _claude_runtime_output(output_file)
        trace = _claude_runtime_trace(output_file)
    elif normalized in {"codex", "codex-cli"}:
        run_dir = _local_runtime_run_dir(task_id, normalized)
        output_file = (run_dir / "final_output.md").resolve()
        output = output_file.read_text(encoding="utf-8", errors="replace") if output_file.is_file() else ""
        if not output.strip():
            output_file = (run_dir / "stdout.jsonl").resolve()
            output = _codex_runtime_output(output_file)
    elif normalized == "evaluator":
        report = _evaluator_runtime_report(task_id)
        output_file = Path(report.get("_output_file") or _provider_task_store_file()).resolve()
        output = str(report.get("markdown") or json.dumps(report, ensure_ascii=False, indent=2))
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
        **({"trace": trace} if trace is not None else {}),
        **({"evaluation_report": report} if report is not None else {}),
    }


def _local_runtime_run_dir(task_id: str, backend: str) -> Path:
    provider_dirs = {
        "claude": "claude-provider",
        "claude-cli": "claude-provider",
        "claude-huawei": "claude-huawei-provider",
        "claude-huawei-cli": "claude-huawei-provider",
        "codex": "codex-provider",
        "codex-cli": "codex-provider",
    }
    provider_dir = provider_dirs.get(backend)
    if not provider_dir:
        raise ValueError(f"Unsupported local runtime backend: {backend}")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", task_id):
        raise ValueError("Invalid local runtime task id")

    candidates: list[tuple[Path, Path]] = []
    for base in _local_runtime_data_dirs():
        provider_root = (base / provider_dir).resolve()
        run_dir = (provider_root / task_id).resolve()
        if provider_root not in run_dir.parents:
            raise ValueError("Invalid local runtime task path")
        candidates.append((provider_root, run_dir))
        if run_dir.exists():
            return run_dir
    return candidates[0][1]


def _local_runtime_data_dirs() -> list[Path]:
    """Search current and legacy runtime data roots for historical local runs."""

    roots: list[Path] = []
    configured = os.environ.get("MAOS_DATA_DIR")
    if configured:
        roots.append(Path(configured).resolve())
    legacy = os.environ.get("MAOS_LEGACY_DATA_DIR", r"D:\dev\MAOS\temporal-data")
    roots.append(Path(legacy).resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _claude_runtime_output(stdout_file: Path) -> str:
    stdout = stdout_file.read_text(encoding="utf-8", errors="replace") if stdout_file.is_file() else ""
    if stdout_file.suffix.lower() == ".jsonl":
        result = _claude_stream_json_final_output(stdout)
        return result if result is not None else readable_claude_partial_output(stdout)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, str) else stdout


def _claude_runtime_output_file(run_dir: Path) -> Path:
    stream_file = (run_dir / "stdout.jsonl").resolve()
    if stream_file.is_file():
        return stream_file
    return (run_dir / "stdout.json").resolve()


def _claude_runtime_trace(stdout_file: Path) -> list[dict[str, Any]]:
    if stdout_file.suffix.lower() != ".jsonl" or not stdout_file.is_file():
        return []
    return readable_claude_trace_from_stream(
        stdout_file.read_text(encoding="utf-8", errors="replace")
    )


def _claude_stream_json_events(stdout: str) -> list[dict[str, Any]]:
    return stream_json_events(stdout)


def _claude_stream_json_final_output(stdout: str) -> str | None:
    return stream_json_final_output(stdout)


def _claude_trace_events_from_stream_event(event: dict[str, Any], index: int) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "event")
    timestamp = event.get("timestamp") or event.get("created_at")
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else event.get("content")
    if isinstance(content, list):
        steps: list[dict[str, Any]] = []
        for part_index, part in enumerate(content, start=1):
            if not isinstance(part, dict):
                continue
            steps.append(_claude_trace_event_from_content_part(part, event_type, timestamp, index, part_index))
        if steps:
            return steps
    return [
        {
            "type": f"claude.{event_type}",
            "title": _claude_stream_event_title(event),
            "description": _claude_stream_event_description(event),
            "timestamp": timestamp,
            "preview": _truncate_preview(json.dumps(event, ensure_ascii=False), 2000),
        }
    ]


def _claude_trace_event_from_content_part(
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
        "preview": _truncate_preview(json.dumps(part, ensure_ascii=False), 2000),
    }


def _claude_stream_event_title(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    return {
        "system": "Claude session initialized",
        "assistant": "Claude assistant message",
        "user": "Claude user/tool-result message",
        "result": "Claude terminal result",
    }.get(event_type, f"Claude stream event: {event_type}")


def _claude_stream_event_description(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if event_type == "result":
        return "The Claude CLI run completed and reported final usage/result metadata."
    if event_type == "system":
        return "The Claude CLI runtime emitted session metadata."
    if event_type in {"assistant", "user"}:
        return "The Claude CLI runtime emitted a conversation message."
    return "The Claude CLI runtime emitted a stream-json event."


def _truncate_preview(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "...<truncated>"


def _codex_runtime_output(stdout_file: Path) -> str:
    stdout = stdout_file.read_text(encoding="utf-8", errors="replace") if stdout_file.is_file() else ""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if '"message"' in line or '"content"' in line or '"text"' in line:
            return line
    return stdout


def _evaluator_runtime_report(task_id: str) -> dict[str, Any]:
    stored = load_evaluation_report(task_id)
    if stored:
        return _normalize_evaluator_report(
            {
                **stored,
                "_output_file": execution_store_db_file(),
            }
        )

    try:
        task = _task_from_provider_task_store(task_id)
    except FileNotFoundError:
        return _evaluator_runtime_report_from_archive(task_id)
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        archived = _evaluator_runtime_report_from_archive(task_id)
        if archived:
            return archived
        raise FileNotFoundError(f"Evaluator task artifact not found: {task_id}")
    for artifact in artifacts:
        for part in artifact.get("parts", []) if isinstance(artifact, dict) else []:
            data = part.get("data") if isinstance(part, dict) else None
            if not isinstance(data, dict):
                continue
            report = data.get("report")
            if isinstance(report, dict):
                return _normalize_evaluator_report({
                    **report,
                    "config": (task.get("metadata") or {}).get("evaluationConfig", {}),
                    "evaluation_task_id": task_id,
                })
            if data.get("agent_backend") == "evaluator":
                return _normalize_evaluator_report({
                    "status": data.get("status"),
                    "benchmark_id": data.get("benchmark_id"),
                    "winner": data.get("winner"),
                    "score_delta": data.get("score_delta"),
                    "single": data.get("single"),
                    "multi": data.get("multi"),
                    "markdown": data.get("latest_comment") or data.get("stdout") or "",
                    "config": (task.get("metadata") or {}).get("evaluationConfig", {}),
                    "evaluation_task_id": task_id,
                })
    archived = _evaluator_runtime_report_from_archive(task_id)
    if archived:
        return archived
    raise FileNotFoundError(f"Evaluator report not found: {task_id}")


def _evaluator_runtime_report_from_archive(task_id: str) -> dict[str, Any]:
    raw_report = _evaluator_runtime_report_from_raw_archive(task_id)
    if raw_report:
        return raw_report

    try:
        from maos_runtime.sandbox.task_archive import load_archived_tasks

        tasks = load_archived_tasks(limit=None)
    except Exception as exc:
        raise FileNotFoundError(f"Evaluator task archive could not be queried: {task_id}") from exc

    for task in tasks:
        node = _archived_node_for_runtime_task(task, task_id, "evaluator")
        if not node:
            continue
        payload = _archived_payload_for_node(task, str(node.get("id") or ""))
        report = _evaluator_report_from_payload(task_id, payload, node, task)
        if report:
            return report
    raise FileNotFoundError(f"Evaluator task not found in provider task store or archive: {task_id}")


def _evaluator_runtime_report_from_raw_archive(task_id: str) -> dict[str, Any]:
    for task in _raw_archived_tasks():
        node = _archived_node_for_runtime_task(task, task_id, "evaluator")
        if not node:
            continue
        node_id = str(node.get("id") or "")
        payload = _archived_payload_for_node(task, node_id)
        report = _evaluator_report_from_payload(task_id, payload, node, task)
        if report:
            return report
    return {}


def _raw_archived_tasks() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in _archive_candidate_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                task = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(task, dict):
                rows.append(task)
    return rows


def _archive_candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    configured = os.environ.get("MAOS_DATA_DIR")
    if configured:
        dirs.append(Path(configured).resolve() / "task-archive")
    dirs.append(Path(os.environ.get("MAOS_LEGACY_DATA_DIR", r"D:\dev\MAOS\temporal-data")).resolve() / "task-archive")
    for root in _local_runtime_data_dirs():
        dirs.append(root / "task-archive")

    unique: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            unique.append(directory)
            seen.add(key)
    return unique


def _archived_node_for_runtime_task(
    task: dict[str, Any],
    task_id: str,
    backend: str,
) -> dict[str, Any] | None:
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    for node in state.get("nodes", []) if isinstance(state.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        if str(node.get("backend") or "").lower() != backend:
            continue
        runtime_ids = {
            node.get("a2a_task_id"),
            node.get("evaluator_task_id"),
            node.get("current_instance_id"),
        }
        for instance in node.get("instances", []) if isinstance(node.get("instances"), list) else []:
            if isinstance(instance, dict):
                runtime_ids.add(instance.get("a2a_task_id"))
                runtime_ids.add(instance.get("evaluator_task_id"))
        if task_id in {str(value) for value in runtime_ids if value}:
            return node
    return None


def _archived_payload_for_node(task: dict[str, Any], node_id: str) -> dict[str, Any]:
    containers: list[dict[str, Any]] = []
    for key in ("result", "state"):
        value = task.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in list(containers):
        nested = container.get("state")
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        results = container.get("results")
        if isinstance(results, dict) and isinstance(results.get(node_id), dict):
            return results[node_id]
        instance_results = container.get("instance_results")
        if isinstance(instance_results, dict):
            if isinstance(instance_results.get(node_id), dict):
                return instance_results[node_id]
            for key, value in reversed(list(instance_results.items())):
                if str(key).startswith(f"{node_id}#") and isinstance(value, dict):
                    return value
    return {}


def _evaluator_report_from_payload(
    task_id: str,
    payload: dict[str, Any],
    node: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    report = payload.get("report") if isinstance(payload, dict) else None
    if isinstance(report, dict):
        return _normalize_evaluator_report({
            **report,
            "evaluation_task_id": report.get("evaluation_task_id") or task_id,
            "status": report.get("status") or payload.get("status") or node.get("status"),
        })

    single = payload.get("single") if isinstance(payload.get("single"), dict) else {}
    multi = payload.get("multi") if isinstance(payload.get("multi"), dict) else {}
    if not single and payload.get("single_score") is not None:
        single = {"score": payload.get("single_score")}
    if not multi and payload.get("multi_score") is not None:
        multi = {"score": payload.get("multi_score")}
    benchmark_id = payload.get("benchmark_id")
    winner = payload.get("winner") or payload.get("decision")
    if not any([benchmark_id, winner, single, multi]):
        return {}
    score_delta = payload.get("score_delta")
    if score_delta is None and single.get("score") is not None and multi.get("score") is not None:
        try:
            score_delta = round(float(multi["score"]) - float(single["score"]), 2)
        except (TypeError, ValueError):
            score_delta = None
    report = {
        "status": payload.get("status") or node.get("status") or task.get("status"),
        "benchmark_id": benchmark_id,
        "evaluation_task_id": task_id,
        "winner": winner,
        "score_delta": score_delta,
        "single": single,
        "multi": multi,
        "comparison_sources": payload.get("comparison_sources") or {},
        "archived_fallback": True,
        "source_task_id": task.get("task_id") or task.get("workflow_id"),
    }
    markdown = payload.get("latest_comment") or payload.get("stdout")
    report["markdown"] = markdown if isinstance(markdown, str) and markdown.strip() else _minimal_evaluator_markdown(report)
    return _normalize_evaluator_report(report)


def _normalize_evaluator_report(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return report
    normalized = dict(report)
    benchmark_id = str(normalized.get("benchmark_id") or "")
    test_names = _benchmark_hidden_test_names(benchmark_id)
    for side in ("single", "multi"):
        candidate = normalized.get(side)
        if isinstance(candidate, dict):
            normalized[side] = _candidate_with_hidden_test_cases(
                candidate,
                test_names,
                _candidate_workspace(normalized, side),
            )
    if not isinstance(normalized.get("test_matrix"), dict):
        normalized["test_matrix"] = _hidden_test_matrix_from_candidates(
            normalized.get("single") if isinstance(normalized.get("single"), dict) else {},
            normalized.get("multi") if isinstance(normalized.get("multi"), dict) else {},
            test_names,
        )
    return normalized


def _benchmark_hidden_test_names(benchmark_id: str) -> list[str]:
    if not benchmark_id:
        return []
    path = (
        Path(__file__).resolve().parents[1]
        / "maos_runtime"
        / "evaluation"
        / "benchmarks"
        / benchmark_id
        / "hidden_tests.py"
    )
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return re.findall(r"(?m)^def\s+(test_[A-Za-z0-9_]+)\s*\(", text)


def _candidate_with_hidden_test_cases(
    candidate: dict[str, Any],
    test_names: list[str],
    workspace: Path | None,
) -> dict[str, Any]:
    existing_cases = candidate.get("hidden_test_cases")
    if isinstance(existing_cases, list) and existing_cases:
        cases = [
            item
            for item in existing_cases
            if isinstance(item, dict) and item.get("name")
        ]
    else:
        hidden = _find_eval_command(candidate, "hidden_pytest")
        cases = _pytest_case_results_from_command(hidden, test_names)
    if not cases:
        return candidate
    cases = _recover_case_results(candidate, cases, workspace)
    updated = dict(candidate)
    updated["hidden_test_cases"] = cases
    return updated


def _candidate_workspace(report: dict[str, Any], side: str) -> Path | None:
    workspace = report.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        return None
    path = Path(workspace) / side
    return path if path.is_dir() else None


def _recover_case_results(
    candidate: dict[str, Any],
    cases: list[dict[str, str]],
    workspace: Path | None,
) -> list[dict[str, str]]:
    statuses = {
        str(item.get("name")): str(item.get("status") or "unknown")
        for item in cases
        if item.get("name")
    }
    test_names = [str(item.get("name")) for item in cases if item.get("name")]

    for name, status in _pytest_lastfailed_statuses(workspace, test_names).items():
        if status == "failed" and statuses.get(name) in {"unknown", "not_run", "passed"}:
            statuses[name] = "failed"

    statuses = _fill_unknown_statuses_from_counts(statuses, candidate)
    unresolved = [
        name
        for name in test_names
        if statuses.get(name) in {"unknown", "not_run"}
    ]
    if unresolved:
        statuses.update(_rerun_unknown_hidden_tests(workspace, unresolved))
        statuses = _fill_unknown_statuses_from_counts(statuses, candidate)

    return [
        {
            "name": name,
            "display": _compact_test_name(name),
            "status": statuses.get(name, "unknown"),
        }
        for name in test_names
    ]


def _pytest_lastfailed_statuses(
    workspace: Path | None,
    test_names: list[str],
) -> dict[str, str]:
    if not workspace:
        return {}
    path = workspace / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    known = set(test_names)
    statuses: dict[str, str] = {}
    for key in payload:
        name = str(key).rsplit("::", 1)[-1]
        if name in known:
            statuses[name] = "failed"
    return statuses


def _fill_unknown_statuses_from_counts(
    statuses: dict[str, str],
    candidate: dict[str, Any],
) -> dict[str, str]:
    counts = candidate.get("hidden_tests") if isinstance(candidate.get("hidden_tests"), dict) else {}
    expected_failed = int(counts.get("failed") or 0)
    expected_errors = int(counts.get("errors") or 0)
    if expected_errors:
        return statuses
    known_failed = sum(1 for status in statuses.values() if status in {"failed", "timeout"})
    unknown = [name for name, status in statuses.items() if status in {"unknown", "not_run"}]
    if not unknown:
        return statuses
    if known_failed == expected_failed:
        filled = dict(statuses)
        for name in unknown:
            filled[name] = "passed"
        return filled
    return statuses


def _rerun_unknown_hidden_tests(
    workspace: Path | None,
    test_names: list[str],
) -> dict[str, str]:
    if not workspace or not test_names:
        return {}
    if os.environ.get("MAOS_EVALUATOR_RECOVER_UNKNOWN_CASES", "1").lower() in {"0", "false", "no"}:
        return {}
    if not (workspace / "maos_cache.py").is_file() or not (workspace / "hidden_tests.py").is_file():
        return {}

    cache_file = workspace / ".maos-hidden-test-case-recovery.json"
    cached = _read_case_recovery_cache(cache_file)
    missing = [name for name in test_names if name not in cached]
    if missing:
        cached.update(_run_hidden_test_items(workspace, missing))
        _write_case_recovery_cache(cache_file, cached)
    return {name: cached[name] for name in test_names if name in cached}


def _read_case_recovery_cache(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): str(status)
        for name, status in payload.items()
        if str(status) in {"passed", "failed", "error", "timeout", "skipped"}
    }


def _write_case_recovery_cache(path: Path, statuses: dict[str, str]) -> None:
    try:
        path.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def _run_hidden_test_items(
    workspace: Path,
    test_names: list[str],
) -> dict[str, str]:
    timeout = float(os.environ.get("MAOS_EVALUATOR_RECOVER_ITEM_TIMEOUT_SECONDS", "8"))
    statuses: dict[str, str] = {}
    for name in test_names:
        command = [
            sys.executable,
            "-m",
            "pytest",
            f"hidden_tests.py::{name}",
            "-q",
            "--tb=short",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            statuses[name] = "timeout"
            continue
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0:
            statuses[name] = "passed"
        elif re.search(r"\b(error|errors)\b", output, flags=re.IGNORECASE):
            statuses[name] = "error"
        elif re.search(r"\bskipped\b", output, flags=re.IGNORECASE):
            statuses[name] = "skipped"
        else:
            statuses[name] = "failed"
    return statuses


def _find_eval_command(candidate: dict[str, Any], name: str) -> dict[str, Any]:
    commands = candidate.get("commands") if isinstance(candidate.get("commands"), list) else []
    for command in commands:
        if isinstance(command, dict) and command.get("name") == name:
            return command
    return {}


def _pytest_case_results_from_command(
    command: dict[str, Any],
    test_names: list[str],
) -> list[dict[str, str]]:
    if not test_names:
        return []
    if not isinstance(command, dict) or not command:
        return [
            {"name": name, "display": _compact_test_name(name), "status": "not_run"}
            for name in test_names
        ]
    text = f"{command.get('stdout') or ''}\n{command.get('stderr') or ''}"
    explicit = {
        name: status
        for name, status in re.findall(
            r"MAOS_PYTEST_CASE\s+name=([A-Za-z0-9_]+)\s+status=([A-Za-z_]+)",
            text,
        )
    }
    if explicit:
        return _case_results_for_names(explicit, test_names)

    line_statuses: dict[str, str] = {}
    for status, name in re.findall(
        r"\b(PASSED|FAILED|ERROR|SKIPPED)\s+hidden_tests\.py::([A-Za-z0-9_]+)",
        text,
    ):
        line_statuses[name] = {
            "PASSED": "passed",
            "FAILED": "failed",
            "ERROR": "error",
            "SKIPPED": "skipped",
        }.get(status, "unknown")

    progress = "".join(re.findall(r"(?m)^([.FEsxX]+)\s+\[[^\]]*%\]", text))
    if progress:
        statuses = {
            name: _pytest_code_status(progress[index])
            for index, name in enumerate(test_names)
            if index < len(progress)
        }
        statuses.update(line_statuses)
        for name in test_names[len(progress):]:
            statuses.setdefault(name, "unknown")
        return _case_results_for_names(statuses, test_names)

    failed_names = set(re.findall(r"FAILED\s+hidden_tests\.py::([A-Za-z0-9_]+)", text))
    error_names = set(re.findall(r"ERROR\s+hidden_tests\.py::([A-Za-z0-9_]+)", text))
    if failed_names or error_names or line_statuses:
        statuses = dict(line_statuses)
        statuses.update(
            {
                name: "error" if name in error_names else "failed" if name in failed_names else statuses.get(name, "unknown")
                for name in test_names
            }
        )
        return _case_results_for_names(
            statuses,
            test_names,
        )

    if bool(command.get("passed")):
        return _case_results_for_names({name: "passed" for name in test_names}, test_names)
    return _case_results_for_names({name: "unknown" for name in test_names}, test_names)


def _case_results_for_names(
    statuses: dict[str, str],
    test_names: list[str],
) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "display": _compact_test_name(name),
            "status": statuses.get(name, "unknown"),
        }
        for name in test_names
    ]


def _pytest_code_status(code: str) -> str:
    return {
        ".": "passed",
        "F": "failed",
        "E": "error",
        "s": "skipped",
        "x": "skipped",
        "X": "failed",
    }.get(code, "unknown")


def _compact_test_name(name: str) -> str:
    text = str(name).removeprefix("test_")
    text = text.replace("async_ttl_cache", "async cache")
    text = text.replace("aget_or_set", "aget/set")
    text = text.replace("get_or_set", "get/set")
    text = text.replace("_", " ")
    return text if len(text) <= 34 else text[:31].rstrip() + "..."


def _hidden_test_matrix_from_candidates(
    single: dict[str, Any],
    multi: dict[str, Any],
    test_names: list[str],
) -> dict[str, Any]:
    single_cases = _case_map(single)
    multi_cases = _case_map(multi)
    ordered_names = list(test_names)
    for cases in (single_cases, multi_cases):
        for name in cases:
            if name not in ordered_names:
                ordered_names.append(name)
    return {
        "tests": [
            {
                "name": name,
                "display": (
                    single_cases.get(name, {}).get("display")
                    or multi_cases.get(name, {}).get("display")
                    or _compact_test_name(name)
                ),
            }
            for name in ordered_names
        ],
        "rows": [
            {
                "candidate": "single",
                "label": "Single Agent",
                "statuses": {
                    name: single_cases.get(name, {}).get("status", "unknown")
                    for name in ordered_names
                },
            },
            {
                "candidate": "multi",
                "label": "Multi Agent",
                "statuses": {
                    name: multi_cases.get(name, {}).get("status", "unknown")
                    for name in ordered_names
                },
            },
        ],
    }


def _case_map(candidate: dict[str, Any]) -> dict[str, dict[str, str]]:
    cases = candidate.get("hidden_test_cases") if isinstance(candidate, dict) else None
    if not isinstance(cases, list):
        return {}
    mapped: dict[str, dict[str, str]] = {}
    for item in cases:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        mapped[name] = {
            "name": name,
            "display": str(item.get("display") or _compact_test_name(name)),
            "status": str(item.get("status") or "unknown"),
        }
    return mapped


def _minimal_evaluator_markdown(report: dict[str, Any]) -> str:
    single = report.get("single") if isinstance(report.get("single"), dict) else {}
    multi = report.get("multi") if isinstance(report.get("multi"), dict) else {}
    return "\n".join(
        [
            "# Deterministic Evaluation Result",
            "",
            f"- Benchmark: `{report.get('benchmark_id') or '-'}`",
            f"- Winner: `{report.get('winner') or '-'}`",
            f"- Single score: `{single.get('score', '-')}`",
            f"- Multi score: `{multi.get('score', '-')}`",
            f"- Score delta: `{report.get('score_delta', '-')}`",
            "",
            "This report was reconstructed from the archived workflow payload because the evaluator provider task record is no longer available.",
        ]
    )


def _task_from_provider_task_store(task_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", task_id):
        raise ValueError("Invalid local runtime task id")
    try:
        from maos_runtime.a2a_task_store import task_record_for_provider_task_id

        record = task_record_for_provider_task_id(task_id)
    except Exception as exc:
        raise FileNotFoundError(f"Provider task store could not be queried: {task_id}") from exc
    if record is None:
        record = _task_record_from_provider_task_store_fallbacks(task_id)
    if isinstance(record, dict):
        task = record.get("task")
        if isinstance(task, dict):
            return task
    raise FileNotFoundError(f"Local runtime task not found in provider task store: {task_id}")


def _task_record_from_provider_task_store_fallbacks(task_id: str) -> dict[str, Any] | None:
    for db_file in _provider_task_store_candidate_files():
        record = _task_record_from_sqlite_db(task_id, db_file)
        if record is not None:
            return record
    return None


def _provider_task_store_candidate_files() -> list[Path]:
    files: list[Path] = []
    configured = os.environ.get("A2A_PROVIDER_TASK_DB_FILE") or os.environ.get("MAOS_RUNTIME_DB_FILE")
    if configured:
        files.append(Path(configured).resolve())
    for root in _local_runtime_data_dirs():
        files.append((root / "maos_runtime.db").resolve())
        files.append((root / "provider_tasks.sqlite3").resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in files:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _task_record_from_sqlite_db(task_id: str, db_file: Path) -> dict[str, Any] | None:
    if not db_file.is_file():
        return None
    try:
        with sqlite3.connect(db_file) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM provider_tasks WHERE provider_task_id = ?",
                (task_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        task = json.loads(row["task_json"])
    except Exception:
        return None
    return {
        "idempotency_key": row["idempotency_key"],
        "workflow_id": row["workflow_id"],
        "node_id": row["node_id"],
        "backend": row["backend"],
        "a2a_task_id": row["provider_task_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result_artifact_created": bool(row["result_artifact_created"]),
        "task": task,
    }


def _provider_task_store_file() -> str:
    from maos_runtime.a2a_task_store import provider_task_db_file

    return provider_task_db_file()

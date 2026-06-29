"""Deterministic benchmark runner used by the evaluator provider."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from maos_runtime.evaluation.extraction import extract_python_files
from maos_runtime.evaluation.sandbox import (
    copy_file,
    make_eval_workspace,
    python_command,
    run_command,
    write_files,
)
from maos_runtime.http_json_client import request_json
from maos_runtime.runtime_config import sandbox_api_base


BENCHMARKS = {
    "async_ttl_cache": {
        "required_files": ("maos_cache.py",),
        "hidden_tests": Path(__file__).parent / "benchmarks" / "async_ttl_cache" / "hidden_tests.py",
    }
}


def run_evaluation(config: dict[str, Any]) -> dict[str, Any]:
    benchmark_id = str(config.get("benchmark_id") or "async_ttl_cache")
    if benchmark_id not in BENCHMARKS:
        raise ValueError(f"Unsupported benchmark_id: {benchmark_id}")
    api_base = str(config.get("sandbox_api_base") or sandbox_api_base())
    config = _resolve_evaluation_config(config, api_base)
    benchmark = BENCHMARKS[benchmark_id]
    single_output = _candidate_output(config, "single", api_base)
    multi_output = _candidate_output(config, "multi", api_base)

    workspace = make_eval_workspace()
    single = _evaluate_candidate(
        "single",
        single_output,
        benchmark=benchmark,
        workspace=workspace / "single",
    )
    multi = _evaluate_candidate(
        "multi",
        multi_output,
        benchmark=benchmark,
        workspace=workspace / "multi",
    )
    winner = _winner(single["score"], multi["score"])
    report = {
        "status": "completed",
        "benchmark_id": benchmark_id,
        "workspace": str(workspace),
        "comparison_sources": _comparison_sources(config),
        "single": single,
        "multi": multi,
        "winner": winner,
        "score_delta": round(float(multi["score"]) - float(single["score"]), 2),
        "rubric": {
            "extract": 10,
            "compile": 10,
            "hidden_tests": 70,
            "docs": 10,
        },
    }
    report["markdown"] = _markdown_report(report)
    return report


def _comparison_sources(config: dict[str, Any]) -> dict[str, Any]:
    def source(prefix: str) -> dict[str, Any]:
        selector = config.get(f"{prefix}_selector")
        return {
            "task_id": config.get(f"{prefix}_task_id"),
            "node_id": config.get(f"{prefix}_node_id"),
            "selector": selector if isinstance(selector, dict) else None,
            "resolved_from_selector": bool(config.get(f"{prefix}_resolved_from_selector")),
            "graph_id": config.get(f"{prefix}_graph_id"),
            "graph_name": config.get(f"{prefix}_graph_name"),
            "has_direct_output": isinstance(config.get(f"{prefix}_output"), str)
            and bool(str(config.get(f"{prefix}_output")).strip()),
        }

    return {
        "single": source("single"),
        "multi": source("multi"),
    }


def _resolve_evaluation_config(config: dict[str, Any], api_base: str) -> dict[str, Any]:
    resolved = dict(config)
    for prefix in ("single", "multi"):
        if _has_direct_output(resolved, prefix) or resolved.get(f"{prefix}_task_id"):
            continue
        selector = resolved.get(f"{prefix}_selector")
        if not isinstance(selector, dict):
            continue
        task = _latest_task_for_selector(selector, api_base, prefix)
        node_id = selector.get("node_id") or selector.get("final_node_id")
        resolved[f"{prefix}_task_id"] = task.get("task_id") or task.get("workflow_id")
        if node_id and not resolved.get(f"{prefix}_node_id"):
            resolved[f"{prefix}_node_id"] = node_id
        resolved[f"{prefix}_graph_id"] = task.get("graph_id")
        resolved[f"{prefix}_graph_name"] = task.get("graph_name")
        resolved[f"{prefix}_resolved_from_selector"] = True
    return resolved


def _has_direct_output(config: dict[str, Any], prefix: str) -> bool:
    return isinstance(config.get(f"{prefix}_output"), str) and bool(
        str(config.get(f"{prefix}_output")).strip()
    )


def _candidate_output(config: dict[str, Any], prefix: str, api_base: str) -> str:
    direct = config.get(f"{prefix}_output")
    if isinstance(direct, str) and direct.strip():
        return direct
    task_id = config.get(f"{prefix}_task_id")
    if not task_id:
        raise ValueError(f"Missing {prefix}_task_id or {prefix}_output")
    node_id = config.get(f"{prefix}_node_id")
    task = request_json(api_base, "GET", f"/api/tasks/{task_id}")
    return _output_from_task(task, node_id=node_id, api_base=api_base)


def _latest_task_for_selector(
    selector: dict[str, Any],
    api_base: str,
    prefix: str,
) -> dict[str, Any]:
    response = request_json(api_base, "GET", "/api/tasks")
    tasks = response.get("tasks") if isinstance(response, dict) else None
    if not isinstance(tasks, list):
        raise ValueError("Sandbox task list response did not include tasks")

    matches = [
        task
        for task in tasks
        if isinstance(task, dict) and _task_matches_selector(task, selector)
    ]
    if not matches:
        raise ValueError(
            f"No completed task matched {prefix}_selector: "
            f"{json.dumps(selector, ensure_ascii=False)}"
        )
    matches.sort(key=_task_sort_time, reverse=True)
    return matches[0]


def _task_matches_selector(task: dict[str, Any], selector: dict[str, Any]) -> bool:
    if not _status_matches(task.get("status"), selector.get("status", "completed")):
        return False
    if not _field_matches(task.get("graph_id"), selector.get("graph_id")):
        return False
    if not _field_matches(task.get("graph_id"), selector.get("graph_ids")):
        return False
    if not _field_matches(task.get("task_id") or task.get("workflow_id"), selector.get("task_id")):
        return False
    task_prefix = selector.get("task_id_prefix")
    if task_prefix and not str(task.get("task_id") or task.get("workflow_id") or "").startswith(str(task_prefix)):
        return False
    graph_name = str(task.get("graph_name") or "")
    if selector.get("graph_name") and graph_name != str(selector["graph_name"]):
        return False
    if selector.get("graph_name_contains") and str(selector["graph_name_contains"]) not in graph_name:
        return False
    excluded = selector.get("exclude_task_ids")
    if isinstance(excluded, list) and str(task.get("task_id") or task.get("workflow_id")) in {
        str(item) for item in excluded
    }:
        return False

    node_id = selector.get("node_id") or selector.get("final_node_id")
    backend = selector.get("backend")
    node_status = selector.get("node_status", "completed")
    if node_id or backend or node_status:
        node = _task_node(task, str(node_id)) if node_id else None
        if node_id and not node:
            return False
        if node is not None:
            if not _status_matches(node.get("status"), node_status):
                return False
            if backend and str(node.get("backend") or "").lower() != str(backend).lower():
                return False
        elif backend:
            nodes = _task_nodes(task)
            if not any(str(node.get("backend") or "").lower() == str(backend).lower() for node in nodes):
                return False
    return True


def _field_matches(value: Any, expected: Any) -> bool:
    if expected is None:
        return True
    if isinstance(expected, list):
        return str(value) in {str(item) for item in expected}
    return str(value) == str(expected)


def _status_matches(value: Any, expected: Any) -> bool:
    if expected is None:
        return True
    if isinstance(expected, list):
        return str(value) in {str(item) for item in expected}
    return str(value) == str(expected)


def _task_node(task: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return next((node for node in _task_nodes(task) if str(node.get("id")) == node_id), None)


def _task_nodes(task: dict[str, Any]) -> list[dict[str, Any]]:
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    if not state and isinstance(result.get("state"), dict):
        state = result["state"]
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    return [node for node in nodes if isinstance(node, dict)]


def _task_sort_time(task: dict[str, Any]) -> float:
    for key in ("updated_at", "finished_at", "submitted_at", "archived_at"):
        value = task.get(key)
        parsed = _parse_time(value)
        if parsed is not None:
            return parsed
    return 0.0


def _parse_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _output_from_task(
    task: dict[str, Any],
    *,
    node_id: str | None,
    api_base: str,
) -> str:
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    if not state and isinstance(result.get("state"), dict):
        state = result["state"]

    selected_node = _select_node(state, node_id)
    if selected_node:
        backend = str(selected_node.get("backend") or "").lower()
        runtime_id = _local_runtime_id(selected_node, backend)
        if runtime_id and backend in {"claude", "codex"}:
            local = _local_runtime_output(api_base, backend, runtime_id)
            if local:
                return local

    payload = _payload_for_node(task, node_id or (selected_node or {}).get("id"))
    output = _text_from_payload(payload)
    if output:
        return output
    raise ValueError(
        f"Could not locate final output for task {task.get('task_id') or task.get('workflow_id')} "
        f"node {node_id or '<auto>'}"
    )


def _select_node(state: dict[str, Any], node_id: str | None) -> dict[str, Any] | None:
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    if node_id:
        return next((node for node in nodes if node.get("id") == node_id), None)
    preferred = {"final_integrator", "single_coding_agent", "final_competitor_report"}
    for node in reversed(nodes):
        if node.get("id") in preferred and node.get("status") == "completed":
            return node
    for node in reversed(nodes):
        if node.get("status") == "completed":
            return node
    return None


def _local_runtime_id(node: dict[str, Any], backend: str) -> str | None:
    if backend == "claude":
        return node.get("claude_task_id") or node.get("a2a_task_id")
    if backend == "codex":
        return node.get("codex_task_id") or node.get("a2a_task_id")
    return None


def _local_runtime_output(api_base: str, backend: str, runtime_id: str) -> str | None:
    try:
        response = request_json(
            api_base,
            "GET",
            "/api/local-runtime-output",
            params={"backend": backend, "task_id": runtime_id},
        )
    except Exception:
        return None
    final_output = response.get("final_output") if isinstance(response, dict) else None
    content = final_output.get("content") if isinstance(final_output, dict) else None
    return content if isinstance(content, str) and content.strip() else None


def _payload_for_node(task: dict[str, Any], node_id: str | None) -> dict[str, Any]:
    containers = []
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
        if isinstance(results, dict) and node_id and isinstance(results.get(node_id), dict):
            return results[node_id]
        instance_results = container.get("instance_results")
        if isinstance(instance_results, dict):
            if node_id and isinstance(instance_results.get(node_id), dict):
                return instance_results[node_id]
            for key, value in reversed(list(instance_results.items())):
                if node_id and not str(key).startswith(f"{node_id}#"):
                    continue
                if isinstance(value, dict):
                    return value
    return {}


def _text_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("latest_comment", "stdout", "result", "output", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _evaluate_candidate(
    label: str,
    output: str,
    *,
    benchmark: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    required_files = tuple(benchmark["required_files"])
    extracted = extract_python_files(output, required_files=required_files)
    write_files(workspace, extracted.files)
    hidden_tests = Path(benchmark["hidden_tests"])
    copy_file(hidden_tests, workspace / "hidden_tests.py")

    commands = []
    compile_result = None
    hidden_result = None
    if extracted.ok:
        compile_result = run_command(
            "py_compile",
            python_command("-m", "py_compile", "maos_cache.py"),
            cwd=workspace,
            timeout_seconds=20,
        )
        commands.append(compile_result.to_dict())
        if compile_result.passed:
            hidden_result = run_command(
                "hidden_pytest",
                python_command("-m", "pytest", "hidden_tests.py", "-q"),
                cwd=workspace,
                timeout_seconds=90,
            )
            commands.append(hidden_result.to_dict())

    hidden_ratio = _pytest_pass_ratio(hidden_result)
    docs_score = _docs_score(output)
    breakdown = {
        "extract": 10 if extracted.ok else 0,
        "compile": 10 if compile_result and compile_result.passed else 0,
        "hidden_tests": round(70 * hidden_ratio, 2),
        "hidden_test_ratio": round(hidden_ratio, 4),
        "docs": docs_score,
    }
    score = sum(
        float(breakdown[key])
        for key in ("extract", "compile", "hidden_tests", "docs")
    )
    return {
        "label": label,
        "score": round(score, 2),
        "score_breakdown": breakdown,
        "extract_ok": extracted.ok,
        "extraction_errors": extracted.errors,
        "output_chars": len(output),
        "files": {
            name: {
                "chars": len(content),
                "lines": len(content.splitlines()),
            }
            for name, content in extracted.files.items()
        },
        "commands": commands,
        "hidden_tests": _pytest_summary(hidden_result),
        "docs_score": docs_score,
    }


def _pytest_pass_ratio(command: Any) -> float:
    if command is None:
        return 0.0
    summary = _pytest_summary(command)
    total = summary["passed"] + summary["failed"] + summary["errors"]
    if command.passed and total == 0:
        return 1.0
    if total == 0:
        return 0.0
    return summary["passed"] / total


def _pytest_summary(command: Any) -> dict[str, int]:
    if command is None:
        return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    text = f"{command.stdout}\n{command.stderr}"
    return {
        "passed": _summary_count(text, "passed"),
        "failed": _summary_count(text, "failed"),
        "errors": _summary_count(text, "error") + _summary_count(text, "errors"),
        "skipped": _summary_count(text, "skipped"),
    }


def _summary_count(text: str, word: str) -> int:
    matches = re.findall(rf"(\d+)\s+{re.escape(word)}\b", text)
    return sum(int(item) for item in matches)


def _docs_score(output: str) -> int:
    lowered = output.lower()
    score = 0
    if any(marker in output for marker in ("设计", "说明", "原理")) or "design" in lowered:
        score += 5
    if any(marker in output for marker in ("示例", "用法", "使用")) or "example" in lowered:
        score += 5
    return score


def _winner(single_score: float, multi_score: float) -> str:
    if multi_score > single_score:
        return "multi"
    if single_score > multi_score:
        return "single"
    return "tie"


def _markdown_report(report: dict[str, Any]) -> str:
    single = report["single"]
    multi = report["multi"]
    lines = [
        "# AsyncTTLCache Deterministic Evaluation",
        "",
        f"- Benchmark: `{report['benchmark_id']}`",
        f"- Single task: `{_source_value(report, 'single', 'task_id')}`; node: `{_source_value(report, 'single', 'node_id')}`",
        f"- Multi task: `{_source_value(report, 'multi', 'task_id')}`; node: `{_source_value(report, 'multi', 'node_id')}`",
        f"- Winner: `{report['winner']}`",
        f"- Single score: **{single['score']}**",
        f"- Multi score: **{multi['score']}**",
        f"- Delta (multi - single): **{report['score_delta']}**",
        "",
        "## Hidden Test Summary",
        "",
        "| Candidate | Passed | Failed | Errors | Skipped |",
        "|---|---:|---:|---:|---:|",
        _summary_row("single", single["hidden_tests"]),
        _summary_row("multi", multi["hidden_tests"]),
        "",
        "## Extraction",
        "",
        f"- Single extract ok: `{single['extract_ok']}`; errors: {json.dumps(single['extraction_errors'], ensure_ascii=False)}",
        f"- Multi extract ok: `{multi['extract_ok']}`; errors: {json.dumps(multi['extraction_errors'], ensure_ascii=False)}",
        "",
        "## Notes",
        "",
        "This report is generated by the deterministic evaluator provider. It does not call an LLM.",
    ]
    return "\n".join(lines)


def _source_value(report: dict[str, Any], side: str, key: str) -> str:
    sources = report.get("comparison_sources")
    if not isinstance(sources, dict):
        return "-"
    source = sources.get(side)
    if not isinstance(source, dict):
        return "-"
    value = source.get(key)
    return str(value) if value else "-"


def _summary_row(label: str, summary: dict[str, int]) -> str:
    return (
        f"| {label} | {summary['passed']} | {summary['failed']} | "
        f"{summary['errors']} | {summary['skipped']} |"
    )

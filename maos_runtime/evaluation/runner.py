"""Deterministic benchmark runner used by the evaluator provider."""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK, task_record_for_provider_task_id
from maos_runtime.evaluation.extraction import extract_python_files
from maos_runtime.evaluation.sandbox import (
    CommandResult,
    copy_file,
    make_eval_workspace,
    python_command,
    run_command,
    write_files,
)
from maos_runtime.http_json_client import request_json
from maos_runtime.runtime_config import sandbox_api_base

HIDDEN_PYTEST_TIMEOUT_SECONDS = 90
HIDDEN_PYTEST_ITEM_TIMEOUT_SECONDS = 8


BENCHMARKS = {
    "async_ttl_cache": {
        "required_files": ("maos_cache.py",),
        "hidden_tests": Path(__file__).parent / "benchmarks" / "async_ttl_cache" / "hidden_tests.py",
        "title": "AsyncTTLCache Deterministic Evaluation",
    },
    "async_ttl_cache_hard_concurrency": {
        "required_files": ("maos_cache.py",),
        "hidden_tests": Path(__file__).parent
        / "benchmarks"
        / "async_ttl_cache_hard_concurrency"
        / "hidden_tests.py",
        "title": "AsyncTTLCache Hard Concurrency Evaluation",
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
        "benchmark_title": benchmark.get("title", benchmark_id),
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
    report["test_matrix"] = _hidden_test_matrix(single, multi)
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
    selected_id = node_id or (selected_node or {}).get("id")
    output = _output_for_node(task, selected_node, selected_id, api_base)
    if output:
        return _safe_final_integrator_output(task, state, selected_node, output, api_base)
    raise ValueError(
        f"Could not locate final output for task {task.get('task_id') or task.get('workflow_id')} "
        f"node {node_id or '<auto>'}"
    )


def _output_for_node(
    task: dict[str, Any],
    node: dict[str, Any] | None,
    node_id: str | None,
    api_base: str,
) -> str:
    if node:
        backend = str(node.get("backend") or "").lower()
        runtime_id = _local_runtime_id(node, backend)
        if runtime_id and backend in {"claude", "claude-huawei", "codex"}:
            provider_output = _provider_task_artifact_output(runtime_id)
            if provider_output and not _looks_truncated(provider_output):
                return provider_output
            local = _local_runtime_output(api_base, backend, runtime_id)
            if local and not _looks_truncated(local):
                return local
            if provider_output:
                return provider_output
            if local:
                return local

    payload = _payload_for_node(task, node_id or (node or {}).get("id"))
    return _text_from_payload(payload)


def _safe_final_integrator_output(
    task: dict[str, Any],
    state: dict[str, Any],
    selected_node: dict[str, Any] | None,
    output: str,
    api_base: str,
) -> str:
    """Avoid evaluating a lossy final handoff when core code is complete.

    In coding comparison graphs the final integrator is an Agent handoff node.
    It may accidentally summarize or regenerate code and drop exported symbols
    that were present in the core implementation. The deterministic evaluator
    should grade the actual implementation artifact instead of a broken handoff.
    """

    if not selected_node or selected_node.get("id") != "final_integrator":
        return output
    if _async_ttl_cache_output_has_public_api(output):
        return output

    for fallback_id in ("core_implementation", "core_revision", "core_draft"):
        fallback_node = _node_by_id(state, fallback_id)
        fallback = _output_for_node(task, fallback_node, fallback_id, api_base)
        if fallback and _async_ttl_cache_output_has_public_api(fallback):
            return fallback
    return output


def _node_by_id(state: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    return next((node for node in nodes if isinstance(node, dict) and node.get("id") == node_id), None)


def _async_ttl_cache_output_has_public_api(output: str) -> bool:
    extracted = extract_python_files(output, required_files=("maos_cache.py",))
    code = extracted.files.get("maos_cache.py", "")
    if not code.strip():
        return False
    return (
        _defines_python_symbol(code, "AsyncTTLCache", {"class"})
        and _defines_python_symbol(code, "CacheInfo", {"class", "assign"})
        and _defines_python_symbol(code, "ttl_cache", {"function"})
        and _defines_python_symbol(code, "async_ttl_cache", {"function"})
    )


def _defines_python_symbol(code: str, name: str, allowed_kinds: set[str]) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _defines_python_symbol_textually(code, name, allowed_kinds)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name and "class" in allowed_kinds:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name and "function" in allowed_kinds:
            return True
        if isinstance(node, ast.Assign) and "assign" in allowed_kinds:
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return True
        if isinstance(node, ast.AnnAssign) and "assign" in allowed_kinds:
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return True
    return False


def _defines_python_symbol_textually(code: str, name: str, allowed_kinds: set[str]) -> bool:
    escaped = re.escape(name)
    patterns = []
    if "class" in allowed_kinds:
        patterns.append(rf"(?m)^\s*class\s+{escaped}\s*[\(:]")
    if "function" in allowed_kinds:
        patterns.append(rf"(?m)^\s*(?:async\s+def|def)\s+{escaped}\s*\(")
    if "assign" in allowed_kinds:
        patterns.append(rf"(?m)^\s*{escaped}\s*(?::[^=]+)?=")
    return any(re.search(pattern, code) for pattern in patterns)


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
    if backend in {"claude", "claude-huawei"}:
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


def _provider_task_artifact_output(runtime_id: str) -> str | None:
    record = None
    with TASKS_LOCK:
        cached = TASKS.get(runtime_id)
        if isinstance(cached, dict):
            record = copy.deepcopy(cached)
    if record is None:
        record = task_record_for_provider_task_id(runtime_id)
    if record is None:
        record = _provider_task_record_from_known_sqlite_files(runtime_id)
    task = record.get("task") if isinstance(record, dict) else None
    if not isinstance(task, dict):
        return None

    candidates: list[str] = []
    for artifact in task.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get("parts") or []:
            if not isinstance(part, dict):
                continue
            data = part.get("data")
            if isinstance(data, dict):
                candidates.extend(_payload_text_candidates(data))
                payload = data.get("payload")
                if isinstance(payload, dict):
                    candidates.extend(_payload_text_candidates(payload))

    if not candidates:
        return None
    full = [candidate for candidate in candidates if not _looks_truncated(candidate)]
    pool = full or candidates
    return max(pool, key=len)


def _payload_text_candidates(payload: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("latest_comment", "stdout", "result", "output", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)
    report = payload.get("report")
    if isinstance(report, dict):
        markdown = report.get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            candidates.append(markdown)
    return candidates


def _provider_task_record_from_known_sqlite_files(runtime_id: str) -> dict[str, Any] | None:
    for db_file in _provider_task_db_candidates():
        record = _provider_task_record_from_sqlite(db_file, runtime_id)
        if record is not None:
            return record
    return None


def _provider_task_db_candidates() -> list[Path]:
    candidates = [
        os.environ.get("A2A_PROVIDER_TASK_DB_FILE"),
        os.environ.get("MAOS_RUNTIME_DB_FILE"),
    ]
    data_dir = os.environ.get("MAOS_DATA_DIR")
    if data_dir:
        candidates.append(str(Path(data_dir) / "maos_runtime.db"))
    legacy_data_dir = os.environ.get("MAOS_LEGACY_DATA_DIR")
    if legacy_data_dir:
        candidates.append(str(Path(legacy_data_dir) / "maos_runtime.db"))
    project_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            str(project_root / "data" / "provider_tasks.sqlite3"),
            str(Path.cwd() / "data" / "provider_tasks.sqlite3"),
            r"D:\dev\MAOS\temporal-data\maos_runtime.db",
        ]
    )

    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _provider_task_record_from_sqlite(db_file: Path, runtime_id: str) -> dict[str, Any] | None:
    if not db_file.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db_file), timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM provider_tasks WHERE provider_task_id = ?",
            (runtime_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    if row is None:
        return None
    try:
        task = json.loads(row["task_json"])
    except Exception:
        return None
    if not isinstance(task, dict):
        return None
    return {
        "task": task,
        "result_artifact_created": bool(row["result_artifact_created"]),
    }


def _looks_truncated(output: str) -> bool:
    return "...<truncated>" in output or "...<summary-truncated>" in output


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
    for key in ("latest_comment", "stdout", "result", "output", "response", "summary"):
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
    test_names = _collect_pytest_function_names(workspace / "hidden_tests.py")

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
            hidden_result = _run_hidden_pytest(workspace)
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
        "hidden_test_cases": _pytest_case_results(hidden_result, test_names),
        "docs_score": docs_score,
    }


def _run_hidden_pytest(workspace: Path) -> CommandResult:
    """Run hidden tests, falling back to per-test isolation when the suite hangs."""

    suite_command = python_command("-m", "pytest", "hidden_tests.py", "-q", "--tb=short")
    suite_result = run_command(
        "hidden_pytest",
        suite_command,
        cwd=workspace,
        timeout_seconds=HIDDEN_PYTEST_TIMEOUT_SECONDS,
    )
    if not _command_timed_out(suite_result):
        return suite_result

    test_names = _collect_pytest_function_names(workspace / "hidden_tests.py")
    if not test_names:
        return suite_result

    item_results = [
        run_command(
            f"hidden_pytest::{test_name}",
            python_command(
                "-m",
                "pytest",
                f"hidden_tests.py::{test_name}",
                "-q",
                "--tb=short",
            ),
            cwd=workspace,
            timeout_seconds=HIDDEN_PYTEST_ITEM_TIMEOUT_SECONDS,
        )
        for test_name in test_names
    ]
    return _aggregate_hidden_pytest_results(suite_result, item_results)


def _collect_pytest_function_names(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return re.findall(r"(?m)^def\s+(test_[A-Za-z0-9_]+)\s*\(", text)


def _aggregate_hidden_pytest_results(
    suite_result: CommandResult,
    item_results: list[CommandResult],
) -> CommandResult:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    case_results: list[dict[str, str]] = []
    details = [
        "Hidden pytest suite timed out; individual tests were rerun with isolated timeouts.",
        f"Suite timeout: {HIDDEN_PYTEST_TIMEOUT_SECONDS}s; per-test timeout: {HIDDEN_PYTEST_ITEM_TIMEOUT_SECONDS}s.",
        "",
        "Timed-out tests are counted as failed tests, not as a failure of the whole evaluation run.",
        "",
    ]
    if suite_result.stdout.strip():
        details.extend(("Original suite stdout before timeout:", suite_result.stdout.strip(), ""))
    if suite_result.stderr.strip():
        details.extend(("Original suite stderr:", suite_result.stderr.strip(), ""))

    for item in item_results:
        test_name = item.name.removeprefix("hidden_pytest::")
        if _command_timed_out(item):
            counts["failed"] += 1
            case_results.append({"name": test_name, "status": "timeout"})
            details.append(
                f"FAILED hidden_tests.py::{test_name} - timed out after {HIDDEN_PYTEST_ITEM_TIMEOUT_SECONDS}s"
            )
            if item.stdout.strip():
                details.append(_indent_block(item.stdout.strip()))
            if item.stderr.strip():
                details.append(_indent_block(item.stderr.strip()))
            details.append("")
            continue

        item_summary = _pytest_summary(item)
        if sum(item_summary.values()) == 0:
            if item.passed:
                item_summary["passed"] = 1
            else:
                item_summary["failed"] = 1
        for key in counts:
            counts[key] += int(item_summary.get(key, 0))

        status = _single_item_case_status(item, item_summary)
        case_results.append({"name": test_name, "status": status})
        display_status = "PASSED" if status == "passed" else status.upper()
        details.append(f"{display_status} hidden_tests.py::{test_name}")
        if not item.passed:
            if item.stdout.strip():
                details.append(_indent_block(item.stdout.strip()))
            if item.stderr.strip():
                details.append(_indent_block(item.stderr.strip()))
        details.append("")

    summary_line = (
        "MAOS_PYTEST_SUMMARY "
        f"passed={counts['passed']} "
        f"failed={counts['failed']} "
        f"errors={counts['errors']} "
        f"skipped={counts['skipped']}"
    )
    details.append(summary_line)
    for item in case_results:
        details.append(f"MAOS_PYTEST_CASE name={item['name']} status={item['status']}")
    details.append(_human_pytest_summary(counts))
    exit_code = 0 if counts["failed"] == 0 and counts["errors"] == 0 else 1
    duration = round(suite_result.duration_seconds + sum(item.duration_seconds for item in item_results), 3)
    return CommandResult(
        name="hidden_pytest",
        command=[
            *suite_result.command,
            "<timeout-fallback-per-test>",
        ],
        exit_code=exit_code,
        stdout="\n".join(details),
        stderr="",
        duration_seconds=duration,
    )


def _command_timed_out(result: CommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return result.exit_code == 124 or "timed out after" in text


def _single_item_case_status(
    command: CommandResult,
    summary: dict[str, int],
) -> str:
    if _command_timed_out(command):
        return "timeout"
    if summary.get("errors", 0) > 0:
        return "error"
    if summary.get("failed", 0) > 0:
        return "failed"
    if summary.get("passed", 0) > 0 or command.passed:
        return "passed"
    if summary.get("skipped", 0) > 0:
        return "skipped"
    return "unknown"


def _human_pytest_summary(counts: dict[str, int]) -> str:
    parts = []
    for key in ("passed", "failed", "errors", "skipped"):
        value = counts.get(key, 0)
        if value:
            word = "error" if key == "errors" and value == 1 else key
            parts.append(f"{value} {word}")
    return ", ".join(parts) if parts else "no tests ran"


def _indent_block(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


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
    maos_summary = re.search(
        r"MAOS_PYTEST_SUMMARY\s+passed=(\d+)\s+failed=(\d+)\s+errors=(\d+)\s+skipped=(\d+)",
        text,
    )
    if maos_summary:
        return {
            "passed": int(maos_summary.group(1)),
            "failed": int(maos_summary.group(2)),
            "errors": int(maos_summary.group(3)),
            "skipped": int(maos_summary.group(4)),
        }
    summary = {
        "passed": _summary_count(text, "passed"),
        "failed": _summary_count(text, "failed"),
        "errors": _summary_count(text, "error") + _summary_count(text, "errors"),
        "skipped": _summary_count(text, "skipped"),
    }
    if sum(summary.values()) == 0:
        progress = _pytest_progress_summary(text)
        if progress is not None:
            return progress
    return summary


def _pytest_case_results(
    command: Any,
    test_names: list[str],
) -> list[dict[str, str]]:
    if command is None:
        return [
            {"name": name, "display": _compact_test_name(name), "status": "not_run"}
            for name in test_names
        ]
    text = f"{command.stdout}\n{command.stderr}"
    explicit = _explicit_pytest_case_results(text)
    if explicit:
        return _case_results_for_names(explicit, test_names)

    progress = _pytest_progress_codes(text)
    if progress:
        statuses: dict[str, str] = {}
        for index, name in enumerate(test_names):
            if index < len(progress):
                statuses[name] = _pytest_code_status(progress[index])
            else:
                statuses[name] = "not_run" if _command_timed_out(command) else "unknown"
        return _case_results_for_names(statuses, test_names)

    summary = _pytest_summary(command)
    if command.passed and summary.get("passed", 0) >= len(test_names):
        return _case_results_for_names({name: "passed" for name in test_names}, test_names)

    failed_names = set(re.findall(r"FAILED\s+hidden_tests\.py::([A-Za-z0-9_]+)", text))
    error_names = set(re.findall(r"ERROR\s+hidden_tests\.py::([A-Za-z0-9_]+)", text))
    if failed_names or error_names:
        return _case_results_for_names(
            {
                name: "error" if name in error_names else "failed" if name in failed_names else "unknown"
                for name in test_names
            },
            test_names,
        )

    return [
        {"name": name, "display": _compact_test_name(name), "status": "unknown"}
        for name in test_names
    ]


def _explicit_pytest_case_results(text: str) -> dict[str, str]:
    return {
        name: status
        for name, status in re.findall(
            r"MAOS_PYTEST_CASE\s+name=([A-Za-z0-9_]+)\s+status=([A-Za-z_]+)",
            text,
        )
    }


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


def _pytest_progress_codes(text: str) -> str:
    return "".join(re.findall(r"(?m)^([.FEsxX]+)\s+\[[^\]]*%\]", text))


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
    text = name.removeprefix("test_")
    replacements = {
        "async_ttl_cache": "async cache",
        "aget_or_set": "aget/set",
        "get_or_set": "get/set",
        "single_flight": "single-flight",
        "inflight": "inflight",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("_", " ")
    return text if len(text) <= 34 else text[:31].rstrip() + "..."


def _pytest_progress_summary(text: str) -> dict[str, int] | None:
    progress = _pytest_progress_codes(text)
    if not progress:
        return None
    return {
        "passed": progress.count("."),
        "failed": progress.count("F"),
        "errors": progress.count("E"),
        "skipped": progress.count("s"),
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


def _hidden_test_matrix(
    single: dict[str, Any],
    multi: dict[str, Any],
) -> dict[str, Any]:
    single_cases = _case_map(single)
    multi_cases = _case_map(multi)
    ordered_names: list[str] = []
    for candidate in (single, multi):
        cases = candidate.get("hidden_test_cases")
        if isinstance(cases, list):
            for item in cases:
                if isinstance(item, dict):
                    name = str(item.get("name") or "")
                    if name and name not in ordered_names:
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
    cases = candidate.get("hidden_test_cases")
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
        f"# {report.get('benchmark_title') or 'AsyncTTLCache Deterministic Evaluation'}",
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
        "## Hidden Test Matrix",
        "",
        *_hidden_test_matrix_markdown(report.get("test_matrix")),
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


def _hidden_test_matrix_markdown(matrix: Any) -> list[str]:
    if not isinstance(matrix, dict):
        return ["No per-test matrix is available."]
    tests = matrix.get("tests") if isinstance(matrix.get("tests"), list) else []
    rows = matrix.get("rows") if isinstance(matrix.get("rows"), list) else []
    if not tests or not rows:
        return ["No per-test matrix is available."]

    header_cells = [
        _md_cell(str(test.get("display") or test.get("name") or "-"))
        for test in tests
        if isinstance(test, dict)
    ]
    lines = [
        "| Candidate | " + " | ".join(header_cells) + " |",
        "|---|" + "|".join("---:" for _ in header_cells) + "|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        statuses = row.get("statuses") if isinstance(row.get("statuses"), dict) else {}
        cells = [
            _status_mark(str(statuses.get(str(test.get("name") or ""), "unknown")))
            for test in tests
            if isinstance(test, dict)
        ]
        lines.append(f"| {_md_cell(str(row.get('label') or row.get('candidate') or '-'))} | " + " | ".join(cells) + " |")
    return lines


def _status_mark(status: str) -> str:
    normalized = status.lower()
    return {
        "passed": "PASS",
        "failed": "FAIL",
        "error": "ERR",
        "timeout": "TIMEOUT",
        "skipped": "SKIP",
        "not_run": "-",
        "unknown": "?",
    }.get(normalized, normalized.upper() or "?")


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


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

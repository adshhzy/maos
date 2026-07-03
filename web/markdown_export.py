"""Export completed task or node outputs as Markdown documents."""

from __future__ import annotations

import os
import re
import time
import json
from pathlib import Path
from typing import Any

from web.web_agent_api import build_agent_trace, build_local_runtime_output


LOCAL_BACKENDS = {
    "claude",
    "claude-cli",
    "claude-huawei",
    "claude-huawei-cli",
    "codex",
    "codex-cli",
    "evaluator",
}
AGENT_SERVICE_BACKENDS = {"multica", "hermes"}


def export_task_markdown(
    task: dict[str, Any],
    *,
    output_dir: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Extract a task/node final output and write it to a Markdown file."""

    selected_node = _select_node(task, node_id)
    content, source = _extract_output(task, selected_node)
    if not content.strip():
        raise FileNotFoundError("No final output is available for this task/node yet")

    directory = _resolve_output_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = _export_filename(task, selected_node)
    path = directory / filename
    document = _markdown_document(task, selected_node, content, source)
    path.write_text(document, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "task_id": task.get("task_id") or task.get("workflow_id"),
        "workflow_id": task.get("workflow_id") or task.get("task_id"),
        "graph_name": task.get("graph_name") or ((task.get("state") or {}).get("graph_name")),
        "node_id": selected_node.get("id"),
        "node_label": selected_node.get("label") or selected_node.get("id"),
        "backend": _backend(selected_node, None),
        "output_path": str(path),
        "output_dir": str(directory),
        "content_chars": len(content),
        "document_chars": len(document),
        "source": source,
    }


def _select_node(task: dict[str, Any], requested_node_id: str | None) -> dict[str, Any]:
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    result_state = (
        (task.get("result") or {}).get("state")
        if isinstance(task.get("result"), dict)
        else {}
    )
    nodes = list(state.get("nodes") or result_state.get("nodes") or [])
    if not nodes:
        raise FileNotFoundError("Task graph nodes are not available")

    if requested_node_id:
        for node in nodes:
            if str(node.get("id")) == requested_node_id:
                return node
        raise FileNotFoundError(f"Node not found in task: {requested_node_id}")

    terminal_ids = _terminal_node_ids(task, nodes)
    candidates = [
        node
        for node in nodes
        if node.get("id") in terminal_ids and _node_has_output_hint(node)
    ]
    if not candidates:
        candidates = [
            node
            for node in nodes
            if node.get("status") == "completed" and _node_has_output_hint(node)
        ]
    if not candidates:
        candidates = [node for node in nodes if node.get("status") == "completed"]
    if not candidates:
        candidates = nodes
    return candidates[-1]


def _terminal_node_ids(task: dict[str, Any], nodes: list[dict[str, Any]]) -> set[Any]:
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    result_state = (
        (task.get("result") or {}).get("state")
        if isinstance(task.get("result"), dict)
        else {}
    )
    edges = list(state.get("edges") or result_state.get("edges") or [])
    source_ids = {
        edge.get("from") or edge.get("source")
        for edge in edges
        if isinstance(edge, dict)
    }
    if not source_ids:
        return {nodes[-1].get("id")}
    return {node.get("id") for node in nodes if node.get("id") not in source_ids}


def _node_has_output_hint(node: dict[str, Any]) -> bool:
    return bool(
        node.get("output_available")
        or node.get("output_chars")
        or node.get("claude_task_id")
        or node.get("codex_task_id")
        or node.get("a2a_task_id")
        or node.get("agent_service_task_id")
        or node.get("hermes_job_id")
    )


def _extract_output(
    task: dict[str, Any],
    node: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    result = _node_result(task, node)
    backend = _backend(node, result)
    runtime_id = _runtime_id(node, result, backend)

    if backend in LOCAL_BACKENDS and runtime_id:
        payload = build_local_runtime_output(runtime_id, backend)
        final_output = payload.get("final_output") or {}
        if backend == "evaluator" and isinstance(payload.get("evaluation_report"), dict):
            content = _rich_evaluator_markdown(payload["evaluation_report"], final_output)
        else:
            content = str(final_output.get("content") or "")
        return content, {
            "kind": "local-runtime-output",
            "backend": backend,
            "runtime_id": runtime_id,
            "output_file": payload.get("output_file"),
        }

    agent_task_id = (
        node.get("agent_service_task_id")
        or (result or {}).get("agent_service_task_id")
    )
    if backend in AGENT_SERVICE_BACKENDS and agent_task_id:
        payload = build_agent_trace(str(agent_task_id))
        final_output = payload.get("final_output") or {}
        content = str(final_output.get("content") or "")
        return content, {
            "kind": "agent-service-trace-output",
            "backend": backend,
            "agent_service_task_id": agent_task_id,
        }

    content = _content_from_result(result)
    return content, {
        "kind": "workflow-result-payload",
        "backend": backend,
        "runtime_id": runtime_id,
    }


def _node_result(task: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    node_id = node.get("id")
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    for container in (result, state):
        results = container.get("results") if isinstance(container, dict) else None
        if isinstance(results, dict) and isinstance(results.get(str(node_id)), dict):
            return results[str(node_id)]
        instance_results = (
            container.get("instance_results") if isinstance(container, dict) else None
        )
        if isinstance(instance_results, dict):
            current_instance_id = node.get("current_instance_id")
            if current_instance_id and isinstance(
                instance_results.get(str(current_instance_id)), dict
            ):
                return instance_results[str(current_instance_id)]
            for instance in reversed(node.get("instances") or []):
                instance_id = instance.get("id") if isinstance(instance, dict) else None
                if instance_id and isinstance(instance_results.get(str(instance_id)), dict):
                    return instance_results[str(instance_id)]
            if isinstance(instance_results.get(str(node_id)), dict):
                return instance_results[str(node_id)]
    return {}


def _backend(node: dict[str, Any], result: dict[str, Any] | None) -> str:
    payload = (result or {}).get("payload") if isinstance(result, dict) else {}
    return str(
        node.get("backend")
        or (result or {}).get("agent_backend")
        or (result or {}).get("backend")
        or (payload or {}).get("agent_backend")
        or "simulator"
    ).strip().lower().replace("_", "-")


def _runtime_id(
    node: dict[str, Any],
    result: dict[str, Any] | None,
    backend: str,
) -> str:
    payload = (result or {}).get("payload") if isinstance(result, dict) else {}
    if backend in {"claude", "claude-cli", "claude-huawei", "claude-huawei-cli"}:
        return str(
            node.get("claude_task_id")
            or (result or {}).get("claude_task_id")
            or (payload or {}).get("claude_task_id")
            or node.get("a2a_task_id")
            or ""
        )
    if backend in {"codex", "codex-cli"}:
        return str(
            node.get("codex_task_id")
            or (result or {}).get("codex_task_id")
            or (payload or {}).get("codex_task_id")
            or node.get("a2a_task_id")
            or ""
        )
    if backend == "evaluator":
        return str(
            node.get("a2a_task_id")
            or (result or {}).get("evaluation_task_id")
            or (result or {}).get("a2a_task_id")
            or ""
        )
    return str(node.get("a2a_task_id") or (result or {}).get("a2a_task_id") or "")


def _content_from_result(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return "" if result is None else str(result)
    for key in ("latest_comment", "stdout", "message", "summary"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    structured = result.get("structured_output")
    if structured is not None:
        return "```json\n" + json.dumps(structured, ensure_ascii=False, indent=2) + "\n```"
    return ""


def _rich_evaluator_markdown(
    report: dict[str, Any],
    final_output: dict[str, Any],
) -> str:
    single = report.get("single") if isinstance(report.get("single"), dict) else {}
    multi = report.get("multi") if isinstance(report.get("multi"), dict) else {}
    rubric = _normalized_rubric(report)
    lines = [
        "# Deterministic Evaluation Result",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Benchmark | `{_md_inline(report.get('benchmark_id') or '-')}` |",
        f"| Status | `{_md_inline(report.get('status') or '-')}` |",
        f"| Winner | `{_md_inline(report.get('winner') or '-')}` |",
        f"| Single score | **{_fmt_number(single.get('score'))}** |",
        f"| Multi score | **{_fmt_number(multi.get('score'))}** |",
        f"| Multi - single | **{_fmt_number(report.get('score_delta'))}** |",
        "",
        "## Score Method",
        "",
        (
            "`score = extract({extract}) + compile({compile}) + "
            "hidden_tests({hidden} * passed / total) + docs({docs})`"
        ).format(
            extract=_fmt_number(rubric["extract"]),
            compile=_fmt_number(rubric["compile"]),
            hidden=_fmt_number(rubric["hidden_tests"]),
            docs=_fmt_number(rubric["docs"]),
        ),
        "",
        "Hidden test total excludes skipped tests from the pass ratio. "
        "A failed or errored hidden test counts against the ratio.",
        "",
        "| Dimension | Max points | Meaning |",
        "|---|---:|---|",
        f"| Extract | {_fmt_number(rubric['extract'])} | Can deterministically extract the required main implementation file. |",
        f"| Compile | {_fmt_number(rubric['compile'])} | The extracted `maos_cache.py` passes `python -m py_compile`. |",
        f"| Hidden tests | {_fmt_number(rubric['hidden_tests'])} | Fixed evaluator-owned pytest cases: points = max points * passed / (passed + failed + errors). |",
        f"| Docs | {_fmt_number(rubric['docs'])} | Presence of design notes and usage/example markers in the final output. |",
        "",
        "## Score Breakdown",
        "",
        "| Candidate | Extract | Compile | Hidden tests | Docs | Total |",
        "|---|---:|---:|---:|---:|---:|",
        _score_breakdown_row("Single Agent", single, rubric),
        _score_breakdown_row("Multi Agent", multi, rubric),
        "",
        "## Comparison Run IDs",
        "",
        "| Candidate | Task / run ID | Node ID | Graph | Input mode |",
        "|---|---|---|---|---|",
    ]
    sources = report.get("comparison_sources") if isinstance(report.get("comparison_sources"), dict) else {}
    lines.extend(
        [
            _source_row("Single Agent", sources.get("single") if isinstance(sources.get("single"), dict) else {}),
            _source_row("Multi Agent", sources.get("multi") if isinstance(sources.get("multi"), dict) else {}),
            "",
            "## Hidden Test Summary",
            "",
            "| Candidate | Score | Passed | Failed | Errors | Skipped | Total | Pass ratio | Exit | Duration | Failure summary |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            _hidden_row("Single Agent", single),
            _hidden_row("Multi Agent", multi),
            "",
            "## Hidden Test Matrix",
            "",
            *_test_matrix_markdown(report, single, multi),
            "",
            "## Extraction And Files",
            "",
            "| Candidate | Extract OK | Output size | Files | Extraction errors |",
            "|---|---|---:|---|---|",
            _extraction_row("Single Agent", single),
            _extraction_row("Multi Agent", multi),
            "",
            "## Command Details And Failure Reasons",
            "",
            _commands_section("Single Agent", single),
            "",
            _commands_section("Multi Agent", multi),
        ]
    )
    final_text = str(final_output.get("content") or report.get("markdown") or "").strip()
    if final_text:
        lines.extend(["", "## Original Evaluator Markdown", "", final_text])
    return "\n".join(lines).rstrip() + "\n"


def _normalized_rubric(report: dict[str, Any]) -> dict[str, float]:
    rubric = report.get("rubric") if isinstance(report.get("rubric"), dict) else {}
    return {
        "extract": _float_or(rubric.get("extract"), 10.0),
        "compile": _float_or(rubric.get("compile"), 10.0),
        "hidden_tests": _float_or(rubric.get("hidden_tests"), 70.0),
        "docs": _float_or(rubric.get("docs"), 10.0),
    }


def _score_breakdown(candidate: dict[str, Any], rubric: dict[str, float]) -> dict[str, float]:
    existing = candidate.get("score_breakdown") or candidate.get("scoreBreakdown")
    if isinstance(existing, dict):
        extract = _float_or(existing.get("extract"), 0.0)
        compile_score = _float_or(existing.get("compile"), 0.0)
        hidden = _float_or(existing.get("hidden_tests"), 0.0)
        docs = _float_or(existing.get("docs"), 0.0)
        return {
            "extract": extract,
            "compile": compile_score,
            "hidden_tests": hidden,
            "hidden_test_ratio": _float_or(existing.get("hidden_test_ratio"), 0.0),
            "docs": docs,
            "total": extract + compile_score + hidden + docs,
        }
    counts = _test_counts(candidate.get("hidden_tests"))
    ratio = _hidden_ratio(counts)
    compile_command = _find_command(candidate, "py_compile")
    extract = rubric["extract"] if candidate.get("extract_ok") else 0.0
    compile_score = rubric["compile"] if compile_command.get("passed") else 0.0
    hidden = rubric["hidden_tests"] * ratio
    docs = _float_or(candidate.get("docs_score"), 0.0)
    return {
        "extract": extract,
        "compile": compile_score,
        "hidden_tests": hidden,
        "hidden_test_ratio": ratio,
        "docs": docs,
        "total": extract + compile_score + hidden + docs,
    }


def _score_breakdown_row(
    label: str,
    candidate: dict[str, Any],
    rubric: dict[str, float],
) -> str:
    breakdown = _score_breakdown(candidate, rubric)
    total = candidate.get("score")
    if total is None:
        total = breakdown["total"]
    return (
        f"| {label} | "
        f"{_fmt_number(breakdown['extract'])} / {_fmt_number(rubric['extract'])} | "
        f"{_fmt_number(breakdown['compile'])} / {_fmt_number(rubric['compile'])} | "
        f"{_fmt_number(breakdown['hidden_tests'])} / {_fmt_number(rubric['hidden_tests'])} "
        f"({_fmt_percent(breakdown['hidden_test_ratio'])}) | "
        f"{_fmt_number(breakdown['docs'])} / {_fmt_number(rubric['docs'])} | "
        f"**{_fmt_number(total)}** |"
    )


def _source_row(label: str, source: dict[str, Any]) -> str:
    task_id = source.get("task_id") or source.get("taskId") or "-"
    node_id = source.get("node_id") or source.get("nodeId") or "-"
    graph = source.get("graph_name") or source.get("graph_id") or "-"
    direct = source.get("has_direct_output") or source.get("hasDirectOutput")
    mode = "direct output" if direct else "task lookup"
    if source.get("resolved_from_selector"):
        mode += " / latest selector"
    return (
        f"| {label} | `{_md_inline(task_id)}` | `{_md_inline(node_id)}` | "
        f"{_md_inline(graph)} | {_md_inline(mode)} |"
    )


def _hidden_row(label: str, candidate: dict[str, Any]) -> str:
    hidden = _find_command(candidate, "hidden_pytest")
    counts = _test_counts(candidate.get("hidden_tests"))
    if not any(counts.values()):
        counts = _parse_pytest_counts(
            f"{hidden.get('stdout') or ''}\n{hidden.get('stderr') or ''}"
        )
    summary = "All hidden tests passed." if hidden.get("passed") else _failure_summary(
        str(hidden.get("stdout") or hidden.get("stderr") or "-")
    )
    return (
        f"| {label} | {_fmt_number(candidate.get('score'))} | {counts['passed']} | "
        f"{counts['failed']} | {counts['errors']} | {counts['skipped']} | "
        f"{_hidden_total(counts)} | {_fmt_percent(_hidden_ratio(counts))} | "
        f"{hidden.get('exit_code') if hidden.get('exit_code') is not None else '-'} | "
        f"{_fmt_number(hidden.get('duration_seconds'))}s | {_md_inline(summary)} |"
    )


def _test_matrix_markdown(
    report: dict[str, Any],
    single: dict[str, Any],
    multi: dict[str, Any],
) -> list[str]:
    matrix = report.get("test_matrix") or report.get("testMatrix")
    if not isinstance(matrix, dict):
        matrix = _build_test_matrix(single, multi)
    tests = matrix.get("tests") if isinstance(matrix.get("tests"), list) else []
    rows = matrix.get("rows") if isinstance(matrix.get("rows"), list) else []
    if not tests or not rows:
        return ["No per-test matrix is available for this evaluator report."]

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
        lines.append(
            f"| {_md_cell(str(row.get('label') or row.get('candidate') or '-'))} | "
            + " | ".join(cells)
            + " |"
        )
    return lines


def _build_test_matrix(
    single: dict[str, Any],
    multi: dict[str, Any],
) -> dict[str, Any]:
    single_cases = _case_map(single)
    multi_cases = _case_map(multi)
    names: list[str] = []
    for cases in (single_cases, multi_cases):
        for name in cases:
            if name not in names:
                names.append(name)
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
            for name in names
        ],
        "rows": [
            {
                "candidate": "single",
                "label": "Single Agent",
                "statuses": {
                    name: single_cases.get(name, {}).get("status", "unknown")
                    for name in names
                },
            },
            {
                "candidate": "multi",
                "label": "Multi Agent",
                "statuses": {
                    name: multi_cases.get(name, {}).get("status", "unknown")
                    for name in names
                },
            },
        ],
    }


def _case_map(candidate: dict[str, Any]) -> dict[str, dict[str, str]]:
    cases = candidate.get("hidden_test_cases") or candidate.get("hiddenTestCases")
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


def _compact_test_name(name: str) -> str:
    text = str(name).removeprefix("test_")
    text = text.replace("async_ttl_cache", "async cache")
    text = text.replace("aget_or_set", "aget/set")
    text = text.replace("get_or_set", "get/set")
    text = text.replace("_", " ")
    return text if len(text) <= 34 else text[:31].rstrip() + "..."


def _status_mark(status: str) -> str:
    normalized = status.lower().replace("_", "-")
    return {
        "passed": "PASS",
        "failed": "FAIL",
        "error": "ERR",
        "timeout": "TIMEOUT",
        "skipped": "SKIP",
        "not-run": "-",
        "not_run": "-",
        "unknown": "?",
    }.get(normalized, normalized.upper() or "?")


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _extraction_row(label: str, candidate: dict[str, Any]) -> str:
    files = candidate.get("files") if isinstance(candidate.get("files"), dict) else {}
    file_text = "<br>".join(
        f"`{_md_inline(name)}` ({meta.get('lines', 0)} lines, {meta.get('chars', 0)} chars)"
        if isinstance(meta, dict)
        else f"`{_md_inline(name)}`"
        for name, meta in files.items()
    ) or "-"
    errors = candidate.get("extraction_errors")
    if isinstance(errors, list) and errors:
        error_text = "<br>".join(_md_inline(item) for item in errors)
    else:
        error_text = "-"
    return (
        f"| {label} | {'yes' if candidate.get('extract_ok') else 'no'} | "
        f"{candidate.get('output_chars') or 0} | {file_text} | {error_text} |"
    )


def _commands_section(label: str, candidate: dict[str, Any]) -> str:
    commands = candidate.get("commands") if isinstance(candidate.get("commands"), list) else []
    if not commands:
        return f"### {label}\n\nNo commands recorded."
    sections = [f"### {label}"]
    for command in commands:
        if not isinstance(command, dict):
            continue
        passed = "PASS" if command.get("passed") else "FAIL"
        command_text = command.get("command")
        if isinstance(command_text, list):
            command_text = " ".join(str(item) for item in command_text)
        stdout = str(command.get("stdout") or "")
        sections.extend(
            [
                "",
                f"#### {command.get('name') or 'command'}",
                "",
                f"- Status: `{passed}`",
                f"- Exit code: `{command.get('exit_code') if command.get('exit_code') is not None else '-'}`",
                f"- Duration: `{_fmt_number(command.get('duration_seconds'))}s`",
                f"- Command: `{_md_inline(command_text or '-')}`",
            ]
        )
        if stdout:
            sections.extend(["", "stdout:", "", "```text", stdout.rstrip(), "```"])
        if not stdout:
            sections.extend(["", "No stdout recorded."])
    return "\n".join(sections)


def _find_command(candidate: dict[str, Any], name: str) -> dict[str, Any]:
    commands = candidate.get("commands") if isinstance(candidate.get("commands"), list) else []
    for command in commands:
        if isinstance(command, dict) and command.get("name") == name:
            return command
    return {}


def _test_counts(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        "passed": int(source.get("passed") or 0),
        "failed": int(source.get("failed") or 0),
        "errors": int(source.get("errors") or 0),
        "skipped": int(source.get("skipped") or 0),
    }


def _parse_pytest_counts(text: str) -> dict[str, int]:
    return {
        "passed": _summary_count(text, "passed"),
        "failed": _summary_count(text, "failed"),
        "errors": _summary_count(text, "error") + _summary_count(text, "errors"),
        "skipped": _summary_count(text, "skipped"),
    }


def _summary_count(text: str, word: str) -> int:
    return sum(int(item) for item in re.findall(rf"(\d+)\s+{re.escape(word)}\b", text))


def _hidden_total(counts: dict[str, int]) -> int:
    return counts["passed"] + counts["failed"] + counts["errors"]


def _hidden_ratio(counts: dict[str, int]) -> float:
    total = _hidden_total(counts)
    return counts["passed"] / total if total else 0.0


def _failure_summary(output: str, max_lines: int = 8) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    interesting = [
        line
        for line in lines
        if "FAILED" in line or "ERROR" in line or "AssertionError" in line or "E   " in line
    ]
    selected = interesting[:max_lines] or lines[:max_lines]
    return " / ".join(selected) if selected else "-"


def _fmt_number(value: Any) -> str:
    number = _float_or(value, None)
    if number is None:
        return "-"
    if abs(number - round(number)) < 0.000001:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _fmt_percent(value: Any) -> str:
    number = _float_or(value, 0.0) or 0.0
    return f"{number * 100:.1f}%"


def _float_or(value: Any, fallback: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _md_inline(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "\\`")


def _resolve_output_dir(output_dir: str | None) -> Path:
    raw = (output_dir or "").strip()
    if not raw:
        raw = os.environ.get("MAOS_MARKDOWN_EXPORT_DIR", "")
    if not raw:
        user_profile = os.environ.get("USERPROFILE")
        raw = str(Path(user_profile) / "Desktop" / "MAOS-exports") if user_profile else "exports"
    return Path(raw).expanduser().resolve()


def _export_filename(task: dict[str, Any], node: dict[str, Any]) -> str:
    graph_name = task.get("graph_name") or ((task.get("state") or {}).get("graph_name")) or "task"
    node_label = node.get("label") or node.get("id") or "result"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    stem = _safe_filename(f"{timestamp}-{graph_name}-{node_label}")
    return f"{stem}.md"


def _safe_filename(value: str, max_length: int = 150) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value)).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .-")
    return cleaned or "maos-result"


def _markdown_document(
    task: dict[str, Any],
    node: dict[str, Any],
    content: str,
    source: dict[str, Any],
) -> str:
    graph_name = task.get("graph_name") or ((task.get("state") or {}).get("graph_name")) or "MAOS Task"
    task_id = task.get("task_id") or task.get("workflow_id") or "-"
    node_label = node.get("label") or node.get("id") or "-"
    node_id = node.get("id") or "-"
    source_lines = [
        f"- Task ID: `{task_id}`",
        f"- Node: `{node_id}` / {node_label}",
        f"- Backend: `{source.get('backend') or '-'}`",
        f"- Source: `{source.get('kind') or '-'}`",
    ]
    for key in ("runtime_id", "agent_service_task_id", "output_file"):
        if source.get(key):
            source_lines.append(f"- {key}: `{source[key]}`")
    return (
        f"# {graph_name}\n\n"
        "## Export Metadata\n\n"
        + "\n".join(source_lines)
        + "\n\n"
        "## Final Output\n\n"
        + content.strip()
        + "\n"
    )

"""Task graph JSON Schema and semantic validation helpers."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "task_graph.schema.json"


@dataclass(frozen=True)
class GraphValidationIssue:
    """One validation problem with a stable path and readable message."""

    path: str
    message: str

    def format(self) -> str:
        return f"{self.path}: {self.message}"


class GraphValidationError(ValueError):
    """Raised when a graph fails JSON Schema or semantic validation."""

    def __init__(self, issues: list[GraphValidationIssue]) -> None:
        self.issues = issues
        super().__init__("Task graph validation failed: " + "; ".join(issue.format() for issue in issues))


_SCHEMA_CACHE: dict[str, Any] | None = None
_VALIDATOR_CACHE: Draft202012Validator | None = None


def load_task_graph_schema() -> dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def validate_task_graph(
    graph: dict[str, Any],
    *,
    validate_json_schema: bool = True,
) -> None:
    issues = collect_task_graph_issues(
        graph,
        validate_json_schema=validate_json_schema,
    )
    if issues:
        raise GraphValidationError(issues)


def collect_task_graph_issues(
    graph: dict[str, Any],
    *,
    validate_json_schema: bool = True,
) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    if validate_json_schema:
        issues.extend(_json_schema_issues(graph))
    if not isinstance(graph, dict):
        return issues or [GraphValidationIssue("$", "Graph must be a JSON object")]
    issues.extend(_semantic_issues(graph))
    return issues


def _json_schema_issues(graph: dict[str, Any]) -> list[GraphValidationIssue]:
    global _VALIDATOR_CACHE
    if _VALIDATOR_CACHE is None:
        _VALIDATOR_CACHE = Draft202012Validator(load_task_graph_schema())
    return [
        GraphValidationIssue(_json_path(error.absolute_path), error.message)
        for error in sorted(_VALIDATOR_CACHE.iter_errors(graph), key=lambda item: list(item.absolute_path))
    ]


def _semantic_issues(graph: dict[str, Any]) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return issues

    node_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if node_id in node_ids:
            duplicate_ids.add(node_id)
        node_ids.add(node_id)
        issues.extend(_node_semantic_issues(index, node, node_ids_hint=None))

    for node_id in sorted(duplicate_ids):
        issues.append(GraphValidationIssue("$.nodes", f"Duplicate node id: {node_id}"))

    explicit_edges = isinstance(graph.get("edges"), list) and bool(graph.get("edges"))
    if explicit_edges:
        issues.extend(_edge_semantic_issues(graph["edges"], node_ids))
    issues.extend(_dependency_semantic_issues(nodes, node_ids))
    issues.extend(_start_semantic_issues(graph, node_ids))
    issues.extend(_condition_semantic_issues(graph))

    if not explicit_edges:
        dependency_edges = [
            {"from": dep, "to": node.get("id")}
            for node in nodes
            if isinstance(node, dict)
            for dep in node.get("deps", [])
        ]
        issues.extend(_acyclic_issues(node_ids, dependency_edges, "$.nodes[*].deps"))
    elif (graph.get("graph_type") or graph.get("type")) == "dag":
        issues.extend(_acyclic_issues(node_ids, graph.get("edges", []), "$.edges"))

    return issues


def _node_semantic_issues(
    index: int,
    node: dict[str, Any],
    node_ids_hint: set[str] | None,
) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    simulate = node.get("simulate")
    if isinstance(simulate, dict):
        min_seconds = simulate.get("min_seconds")
        max_seconds = simulate.get("max_seconds")
        if (
            isinstance(min_seconds, (int, float))
            and isinstance(max_seconds, (int, float))
            and min_seconds > max_seconds
        ):
            issues.append(
                GraphValidationIssue(
                    f"$.nodes[{index}].simulate",
                    "min_seconds must be less than or equal to max_seconds",
                )
            )
    return issues


def _edge_semantic_issues(
    edges: list[Any],
    node_ids: set[str],
) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        from_node = edge.get("from")
        to_node = edge.get("to")
        if from_node not in node_ids:
            issues.append(GraphValidationIssue(f"$.edges[{index}].from", f"Unknown node id: {from_node}"))
        if to_node not in node_ids:
            issues.append(GraphValidationIssue(f"$.edges[{index}].to", f"Unknown node id: {to_node}"))
        key = (str(from_node), str(to_node), str(edge.get("when")))
        if key in seen_edges:
            issues.append(GraphValidationIssue(f"$.edges[{index}]", "Duplicate edge with the same from/to/when"))
        seen_edges.add(key)
    return issues


def _dependency_semantic_issues(
    nodes: list[Any],
    node_ids: set[str],
) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for dep_index, dep in enumerate(node.get("deps", [])):
            if dep not in node_ids:
                issues.append(
                    GraphValidationIssue(
                        f"$.nodes[{index}].deps[{dep_index}]",
                        f"Unknown dependency node id: {dep}",
                    )
                )
    return issues


def _start_semantic_issues(
    graph: dict[str, Any],
    node_ids: set[str],
) -> list[GraphValidationIssue]:
    configured = graph.get("start") if "start" in graph else graph.get("start_nodes")
    if configured is None:
        return []
    start_nodes = [configured] if isinstance(configured, str) else configured
    if not isinstance(start_nodes, list):
        return []
    issues = []
    for index, node_id in enumerate(start_nodes):
        if node_id not in node_ids:
            location = "$.start" if isinstance(configured, str) else f"$.start_nodes[{index}]"
            issues.append(GraphValidationIssue(location, f"Unknown start node id: {node_id}"))
    return issues


def _condition_semantic_issues(graph: dict[str, Any]) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    for index, edge in enumerate(graph.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        when = edge.get("when")
        if isinstance(when, str) and when.strip():
            try:
                ast.parse(when, mode="eval")
            except SyntaxError as exc:
                issues.append(GraphValidationIssue(f"$.edges[{index}].when", f"Invalid condition expression: {exc.msg}"))
    return issues


def _acyclic_issues(
    node_ids: set[str],
    edges: list[dict[str, Any]],
    path: str,
) -> list[GraphValidationIssue]:
    remaining = {node_id: set() for node_id in node_ids}
    for edge in edges:
        from_node = edge.get("from")
        to_node = edge.get("to")
        if from_node in node_ids and to_node in node_ids:
            remaining[to_node].add(from_node)
    completed: set[str] = set()
    while remaining:
        ready = sorted(node_id for node_id, deps in remaining.items() if deps.issubset(completed))
        if not ready:
            cycle_nodes = ", ".join(sorted(remaining))
            return [GraphValidationIssue(path, f"DAG contains a cycle involving: {cycle_nodes}")]
        completed.update(ready)
        for node_id in ready:
            remaining.pop(node_id)
    return []


def _json_path(path_parts: Any) -> str:
    path = "$"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path

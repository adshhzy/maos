"""JSON control-flow graph validation, normalization, and expression helpers."""

import ast
from typing import Any

from maos_runtime.workflows.constants import (
    DEFAULT_MAX_NODE_VISITS,
    DEFAULT_MAX_TOTAL_VISITS,
)


def validate_graph(graph: dict[str, Any]) -> None:
    from maos_runtime.graph.schema import validate_task_graph

    validate_task_graph(graph)
    normalize_graph(graph)


def normalize_graph(graph: dict[str, Any]) -> dict[str, Any]:
    if not graph.get("id"):
        raise ValueError("Graph must define an id")
    if not isinstance(graph.get("nodes"), list) or not graph["nodes"]:
        raise ValueError("Graph must define a non-empty nodes list")

    seen: set[str] = set()
    for node in graph["nodes"]:
        node_id = node.get("id")
        if not node_id:
            raise ValueError("Every node must define an id")
        if node_id in seen:
            raise ValueError(f"Duplicate node id: {node_id}")
        seen.add(node_id)

    explicit_edges = isinstance(graph.get("edges"), list) and bool(graph.get("edges"))
    default_join = graph.get("default_join") or ("any" if explicit_edges else "all")
    edges = _normalize_edges(graph, seen)
    start_nodes = _start_nodes(graph, seen, edges)
    if not start_nodes:
        raise ValueError("Control-flow graph has no start nodes")

    levels = _layout_levels(graph["nodes"], edges, start_nodes, allow_cycles=explicit_edges)
    return {
        **graph,
        "graph_type": graph.get("graph_type") or graph.get("type") or (
            "control_flow" if explicit_edges else "dag"
        ),
        "default_join": default_join,
        "edges": edges,
        "start_nodes": start_nodes,
        "levels": levels,
        "max_total_visits": graph.get("max_total_visits", DEFAULT_MAX_TOTAL_VISITS),
    }


def _normalize_edges(graph: dict[str, Any], node_ids: set[str]) -> list[dict[str, Any]]:
    if isinstance(graph.get("edges"), list) and graph["edges"]:
        edges = []
        for index, edge in enumerate(graph["edges"]):
            from_node = edge.get("from")
            to_node = edge.get("to")
            if from_node not in node_ids:
                raise ValueError(f"Edge {index} references missing from node: {from_node}")
            if to_node not in node_ids:
                raise ValueError(f"Edge {index} references missing to node: {to_node}")
            edges.append(
                {
                    "from": from_node,
                    "to": to_node,
                    "when": edge.get("when"),
                    "label": edge.get("label"),
                    "kind": edge.get("kind", "control"),
                }
            )
        for node in graph["nodes"]:
            for dep in node.get("deps", []):
                if dep not in node_ids:
                    raise ValueError(f"Node {node['id']} depends on missing node {dep}")
        return edges

    edges = []
    for node in graph["nodes"]:
        for dep in node.get("deps", []):
            if dep not in node_ids:
                raise ValueError(f"Node {node['id']} depends on missing node {dep}")
            edges.append({"from": dep, "to": node["id"], "when": None, "label": None, "kind": "dependency"})
    _assert_acyclic([node["id"] for node in graph["nodes"]], edges)
    return edges


def _start_nodes(graph: dict[str, Any], node_ids: set[str], edges: list[dict[str, Any]]) -> list[str]:
    configured = graph.get("start") or graph.get("start_nodes")
    if isinstance(configured, str):
        configured = [configured]
    if configured:
        missing = [node_id for node_id in configured if node_id not in node_ids]
        if missing:
            raise ValueError(f"Start nodes do not exist: {', '.join(missing)}")
        return sorted(configured)
    incoming = {edge["to"] for edge in edges}
    starts = [node["id"] for node in graph["nodes"] if node["id"] not in incoming]
    if starts:
        return starts
    return [graph["nodes"][0]["id"]]


def _assert_acyclic(node_ids: list[str], edges: list[dict[str, Any]]) -> None:
    remaining = {node_id: set() for node_id in node_ids}
    for edge in edges:
        remaining[edge["to"]].add(edge["from"])
    completed: set[str] = set()
    while remaining:
        ready = sorted(node_id for node_id, deps in remaining.items() if deps.issubset(completed))
        if not ready:
            cycle_nodes = ", ".join(sorted(remaining))
            raise ValueError(f"DAG contains a cycle involving: {cycle_nodes}")
        completed.update(ready)
        for node_id in ready:
            remaining.pop(node_id)


def _layout_levels(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    start_nodes: list[str],
    *,
    allow_cycles: bool,
) -> list[list[str]]:
    node_ids = [node["id"] for node in nodes]
    if not allow_cycles:
        return _build_levels_from_edges(node_ids, edges)
    try:
        return _build_levels_from_edges(node_ids, edges)
    except ValueError:
        ordered: list[str] = []
        seen: set[str] = set()
        adjacency = _outgoing_edges(edges)
        queue = list(start_nodes)
        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            ordered.append(node_id)
            for edge in adjacency.get(node_id, []):
                if edge["to"] not in seen:
                    queue.append(edge["to"])
        ordered.extend(node_id for node_id in node_ids if node_id not in seen)
        return [[node_id] for node_id in ordered]


def _build_levels_from_edges(node_ids: list[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    remaining = {node_id: set() for node_id in node_ids}
    for edge in edges:
        remaining[edge["to"]].add(edge["from"])
    completed: set[str] = set()
    levels: list[list[str]] = []
    while remaining:
        ready = sorted(node_id for node_id, deps in remaining.items() if deps.issubset(completed))
        if not ready:
            cycle_nodes = ", ".join(sorted(remaining))
            raise ValueError(f"Graph contains a cycle involving: {cycle_nodes}")
        levels.append(ready)
        completed.update(ready)
        for node_id in ready:
            remaining.pop(node_id)
    return levels


def _outgoing_edges(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge)
    return outgoing


def _predecessor_ids(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    predecessors: dict[str, set[str]] = {}
    for edge in edges:
        predecessors.setdefault(edge["to"], set()).add(edge["from"])
    return predecessors


def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("type") or ("condition" if node.get("operation") == "condition" else "agent"))


def _is_condition_node(node: dict[str, Any]) -> bool:
    return _node_type(node).lower() in {"condition", "decision", "router", "branch"}


def _is_human_node(node: dict[str, Any]) -> bool:
    return _node_type(node).lower() in {"human", "human_node", "human_intervention", "approval"}


def _node_backend_for_display(node: dict[str, Any]) -> str:
    if _is_human_node(node):
        return "human"
    if _is_condition_node(node):
        return "control-flow"
    agent = node.get("agent")
    agent_config = agent if isinstance(agent, dict) else {}
    backend = (
        agent_config.get("backend")
        or node.get("backend")
        or node.get("runtime")
        or "simulator"
    )
    normalized = str(backend).lower().replace("_", "-")
    if normalized in {"agent-service", "agentservice", "real-agent", "multica-daemon"}:
        return "multica"
    if normalized in {"sim", "mock", "simulator"}:
        return "simulator"
    if normalized in {"hermes", "hermes-oneshot", "direct-hermes", "hermes-direct"}:
        return "hermes"
    if normalized in {"codex", "codex-cli", "direct-codex", "codex-direct"}:
        return "codex"
    if normalized in {"claude", "claude-cli", "direct-claude", "claude-direct"}:
        return "claude"
    return normalized


def _node_max_visits(node: dict[str, Any]) -> int:
    value = node.get("max_visits", node.get("max_attempts", DEFAULT_MAX_NODE_VISITS))
    return max(1, int(value))


def _edge_label(edge: dict[str, Any]) -> str:
    if edge.get("label"):
        return str(edge["label"])
    if edge.get("when"):
        return str(edge["when"])
    return ""


def _public_node_spec(node: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if not key.startswith("_")}


def _resolve_value(value: Any, graph_input: dict[str, Any], results: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("$input."):
            return _path_get(graph_input, value.removeprefix("$input."))
        if value.startswith("$deps."):
            return _path_get(results, value.removeprefix("$deps."))
        return value
    if isinstance(value, list):
        return [_resolve_value(item, graph_input, results) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_value(item, graph_input, results) for key, item in value.items()}
    return value


def _path_get(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def _safe_eval_condition(expression: str, context: dict[str, Any]) -> Any:
    tree = ast.parse(expression, mode="eval")
    return _eval_ast(tree.body, _to_attr_context(context))


def _eval_ast(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        if node.id in {"true", "True"}:
            return True
        if node.id in {"false", "False"}:
            return False
        if node.id in {"null", "None"}:
            return None
        raise ValueError(f"Unknown condition variable: {node.id}")
    if isinstance(node, ast.Attribute):
        value = _eval_ast(node.value, context)
        if isinstance(value, dict):
            return value.get(node.attr)
        return getattr(value, node.attr, None)
    if isinstance(node, ast.Subscript):
        value = _eval_ast(node.value, context)
        key = _eval_ast(node.slice, context)
        return value[key]
    if isinstance(node, ast.List):
        return [_eval_ast(item, context) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_ast(item, context) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _eval_ast(key, context): _eval_ast(value, context)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value_node in node.values:
                value = _eval_ast(value_node, context)
                if not value:
                    return value
            return value
        if isinstance(node.op, ast.Or):
            for value_node in node.values:
                value = _eval_ast(value_node, context)
                if value:
                    return value
            return value
    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left, context)
        right = _eval_ast(node.right, context)
        return _eval_binop(node.op, left, right)
    if isinstance(node, ast.Compare):
        left = _eval_ast(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_ast(comparator, context)
            if not _eval_compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Condition functions must be simple names")
        fn_name = node.func.id
        functions = {
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "min": min,
            "max": max,
        }
        if fn_name not in functions:
            raise ValueError(f"Unsupported condition function: {fn_name}")
        args = [_eval_ast(arg, context) for arg in node.args]
        return functions[fn_name](*args)
    raise ValueError(f"Unsupported condition expression: {ast.dump(node)}")


def _eval_binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    raise ValueError(f"Unsupported operator: {type(op).__name__}")


def _eval_compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    raise ValueError(f"Unsupported comparison: {type(op).__name__}")


def _to_attr_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_attr_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_attr_context(item) for item in value]
    return value


def _summarize_result(result: dict[str, Any]) -> str:
    payload = result["payload"]
    interesting_keys = [
        "status",
        "order_status",
        "payment_status",
        "risk",
        "percent",
        "count",
        "tracking_number",
        "channel",
        "reservation_id",
        "artifact",
        "decision",
        "message",
    ]
    summary_parts = [
        f"{key}={payload[key]}" for key in interesting_keys if key in payload
    ]
    if summary_parts:
        return ", ".join(summary_parts)
    return f"keys={','.join(sorted(payload.keys()))}"

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from maos_runtime.dag_workflow import validate_graph
from scripts.run_dag import DEFAULT_SANDBOX_URL, load_graph, request_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit and visualize a JSON control-flow graph through the sandbox API"
    )
    parser.add_argument(
        "graph",
        type=Path,
        nargs="?",
        default=Path("examples/order_processing.json"),
        help="Path to a control-flow graph JSON file",
    )
    parser.add_argument("--sandbox-url", default=DEFAULT_SANDBOX_URL)
    args = parser.parse_args()

    graph = load_graph(args.graph)
    validate_graph(graph)

    response = request_json(
        "POST",
        f"{args.sandbox_url}/api/tasks",
        {"graphs": [graph]},
    )
    task_id = response["task_ids"][0]

    while True:
        task = request_json("GET", f"{args.sandbox_url}/api/tasks/{task_id}")
        render(task["state"], result=task["result"], error=task["error"])
        if task["status"] in {"completed", "failed", "cancelled", "terminated"}:
            break
        time.sleep(1)


def render(
    state: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    os.system("cls" if os.name == "nt" else "clear")

    nodes = {node["id"]: node for node in state["nodes"]}
    print(f"Temporal JSON control-flow execution: {state['graph_name']}")
    print(f"Workflow status: {state['workflow_status']}")
    print()
    print("Graph edges:")
    for edge in state["edges"]:
        print(f"  {edge['from']} -> {edge['to']}")
    print()
    print("Live graph:")
    for level in state["levels"]:
        print("  ".join(format_node(nodes[node_id]) for node_id in level))
        print()

    completed = sum(1 for node in nodes.values() if node["status"] == "completed")
    running = sum(1 for node in nodes.values() if node["status"] == "running")
    suspended = sum(1 for node in nodes.values() if node["status"] == "suspended")
    pending = sum(1 for node in nodes.values() if node["status"] == "pending")
    failed = sum(1 for node in nodes.values() if node["status"] == "failed")
    print(
        "Progress: "
        f"completed={completed} running={running} suspended={suspended} "
        f"pending={pending} failed={failed}"
    )
    print()
    print("Node details:")
    for node in state["nodes"]:
        duration = (
            f"{node['duration_seconds']:.2f}s"
            if node["duration_seconds"] is not None
            else "-"
        )
        deps = ", ".join(node["deps"]) if node["deps"] else "-"
        summary = f" | {node['summary']}" if node["summary"] else ""
        print(
            f"  {status_marker(node['status'])} "
            f"{node['id']:<24} "
            f"op={node['operation']:<22} "
            f"deps=[{deps:<42}] "
            f"duration={duration:<6}{summary}"
        )

    if result is not None:
        print()
        print("Workflow result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    if error:
        print()
        print("Workflow error:")
        print(error)


def format_node(node: dict[str, Any]) -> str:
    marker = status_marker(node["status"])
    duration = (
        f" {node['duration_seconds']:.1f}s"
        if node["duration_seconds"] is not None
        else ""
    )
    return f"[{marker} {node['id']}{duration}]"


def status_marker(status: str) -> str:
    return {
        "pending": "WAIT",
        "running": "RUN ",
        "suspended": "SUSP",
        "completed": "DONE",
        "failed": "FAIL",
    }[status]


if __name__ == "__main__":
    main()

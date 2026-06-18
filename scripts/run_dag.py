import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from maos_runtime.dag_workflow import validate_graph


DEFAULT_SANDBOX_URL = "http://127.0.0.1:8765"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit a JSON control-flow graph to the persistent execution sandbox"
    )
    parser.add_argument("graph", type=Path, help="Path to a control-flow graph JSON file")
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
        if task["status"] in {"completed", "failed", "cancelled", "terminated"}:
            break
        time.sleep(1)

    if task["error"]:
        raise RuntimeError(task["error"])
    print(json.dumps(task["result"], indent=2, ensure_ascii=False))


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(
            "Sandbox API is not reachable. Start it with "
            "`python sandbox_service.py` first."
        ) from exc


def load_graph(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as graph_file:
        return json.load(graph_file)


if __name__ == "__main__":
    main()

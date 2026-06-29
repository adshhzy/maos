import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from maos_runtime.a2a.codex_pool import codex_runtime_pool_status
from web.markdown_export import export_task_markdown
from web.web_agent_api import build_agent_input, build_agent_trace


def make_sandbox_api_handler(
    manager: Any,
    examples_dir: Path,
    simulator_url: str = "http://127.0.0.1:8767",
) -> type[BaseHTTPRequestHandler]:
    """Build the HTTP API facade for the persistent execution sandbox.

    This process intentionally stays light-state: Temporal owns workflow state,
    Agent Service owns real-agent state, and this handler only submits, queries,
    and forwards callback events.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/state", "/api/tasks"}:
                self._send_json(manager.snapshot())
                return
            if parsed.path in {"/agent-trace", "/api/agent-trace"}:
                self._send_agent_trace(parsed)
                return
            if parsed.path in {"/agent-input", "/api/agent-input"}:
                self._send_agent_input(parsed)
                return
            if parsed.path.startswith("/api/tasks/"):
                task_id = parsed.path.rsplit("/", 1)[-1]
                try:
                    self._send_json(manager.task_snapshot(task_id))
                except KeyError:
                    self.send_error(404)
                return
            if parsed.path in {"/examples", "/api/examples"}:
                self._send_json({"examples": list_examples(examples_dir)})
                return
            if parsed.path in {"/example", "/api/example"}:
                self._send_example(parsed, examples_dir)
                return
            if parsed.path in {"/health", "/api/health"}:
                self._send_json(_health_payload(manager, simulator_url))
                return
            if parsed.path in {"/api/provider-status"}:
                self._send_json({"ok": True, "providers": {"codex": codex_runtime_pool_status()}})
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/api/agent-callbacks", "/api/v1/agent-events"}:
                self._handle_agent_callback()
                return
            if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/export-markdown"):
                self._export_markdown(parsed)
                return
            if parsed.path in {"/run-batch", "/api/tasks"}:
                self._submit_tasks()
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_agent_trace(self, parsed: Any) -> None:
            task_id = parse_qs(parsed.query).get("task_id", [""])[0]
            if not task_id:
                self._send_json({"ok": False, "error": "task_id is required"}, status=400)
                return
            try:
                self._send_json(build_agent_trace(task_id))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=502)

        def _send_agent_input(self, parsed: Any) -> None:
            task_id = parse_qs(parsed.query).get("task_id", [""])[0]
            if not task_id:
                self._send_json({"ok": False, "error": "task_id is required"}, status=400)
                return
            try:
                self._send_json(build_agent_input(task_id))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=502)

        def _send_example(self, parsed: Any, root: Path) -> None:
            name = parse_qs(parsed.query).get("name", [""])[0]
            example_path = (root / name).resolve()
            if (
                not name
                or example_path.parent != root.resolve()
                or example_path.suffix.lower() != ".json"
                or not example_path.exists()
            ):
                self.send_error(404)
                return
            self._send_json(load_graph(example_path))

        def _handle_agent_callback(self) -> None:
            try:
                payload = self._read_json_body()
                event = manager.handle_agent_callback(payload)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "event": event})

        def _export_markdown(self, parsed: Any) -> None:
            try:
                task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/export-markdown")
                payload = self._read_json_body()
                task = manager.task_snapshot(task_id)
                result = export_task_markdown(
                    task,
                    output_dir=payload.get("output_dir") if isinstance(payload, dict) else None,
                    node_id=payload.get("node_id") if isinstance(payload, dict) else None,
                )
            except KeyError:
                self.send_error(404)
                return
            except FileNotFoundError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(result)

        def _submit_tasks(self) -> None:
            try:
                payload = self._read_json_body()
                graphs = payload if isinstance(payload, list) else payload["graphs"]
                graphs = _flatten_graph_payloads(graphs)
                task_ids = manager.submit_batch(graphs)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "task_ids": task_ids}, status=201)

        def _read_json_body(self) -> Any:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            return json.loads(raw_body.decode("utf-8"))

        def _send_json(self, body: Any, status: int = 200) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    return Handler


def _health_payload(manager: Any, simulator_url: str) -> dict[str, Any]:
    runtime_info = (
        manager.runtime_info()
        if hasattr(manager, "runtime_info")
        else {"runtime_status": "unknown"}
    )
    return {
        "ok": True,
        "service": "persistent-execution-sandbox-api",
        "simulator": simulator_url,
        "agent_service": os.environ.get("AGENT_SERVICE_API_BASE", "http://127.0.0.1:8091"),
        "runtime": runtime_info,
    }


def load_graph(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as graph_file:
        return json.load(graph_file)


def list_examples(examples_dir: Path) -> list[dict[str, str]]:
    if not examples_dir.exists():
        return []
    examples = []
    for path in sorted(examples_dir.glob("*.json")):
        try:
            graph = load_graph(path)
            examples.append(
                {
                    "file": path.name,
                    "id": graph.get("id", path.stem),
                    "name": graph.get("name", path.stem),
                }
            )
        except Exception:
            examples.append({"file": path.name, "id": path.stem, "name": path.stem})
    return examples


def _flatten_graph_payloads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    graphs: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("graphs"), list):
            graphs.extend(
                graph
                for graph in item["graphs"]
                if isinstance(graph, dict)
            )
        elif isinstance(item, dict):
            graphs.append(item)
    return graphs


def start_sandbox_api_server(
    host: str,
    port: int,
    manager: Any,
    examples_dir: Path,
    simulator_url: str = "http://127.0.0.1:8767",
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port),
        make_sandbox_api_handler(manager, examples_dir, simulator_url),
    )

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from web.web_agent_api import build_agent_input, build_agent_trace
from web.web_ui import INDEX_HTML


def make_handler(
    manager: Any,
    examples_dir: Path,
    simulator_url: str = "http://127.0.0.1:8767",
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
                return
            if parsed.path in {"/state", "/api/tasks"}:
                self._send_json(manager.snapshot())
                return
            if parsed.path in {"/agent-trace", "/api/agent-trace"}:
                params = parse_qs(parsed.query)
                task_id = params.get("task_id", [""])[0]
                if not task_id:
                    self._send_json(
                        {"ok": False, "error": "task_id is required"},
                        status=400,
                    )
                    return
                try:
                    self._send_json(build_agent_trace(task_id))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=502)
                return
            if parsed.path in {"/agent-input", "/api/agent-input"}:
                params = parse_qs(parsed.query)
                task_id = params.get("task_id", [""])[0]
                if not task_id:
                    self._send_json(
                        {"ok": False, "error": "task_id is required"},
                        status=400,
                    )
                    return
                try:
                    self._send_json(build_agent_input(task_id))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=502)
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
                params = parse_qs(parsed.query)
                name = params.get("name", [""])[0]
                example_path = (examples_dir / name).resolve()
                if (
                    not name
                    or example_path.parent != examples_dir.resolve()
                    or example_path.suffix.lower() != ".json"
                    or not example_path.exists()
                ):
                    self.send_error(404)
                    return
                self._send_json(load_graph(example_path))
                return
            if parsed.path in {"/livez", "/api/livez"}:
                self._send_json({"ok": True, "service": "execution-api"})
                return
            if parsed.path in {"/health", "/api/health", "/readyz", "/api/readyz"}:
                health = _health_payload(manager, simulator_url)
                ready = health["runtime"].get("runtime_status") in {"ready", "running"}
                self._send_json(health, status=200 if ready else 503)
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/api/agent-callbacks", "/api/v1/agent-events"}:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    raw_body = self.rfile.read(content_length)
                    payload = json.loads(raw_body.decode("utf-8"))
                    event = manager.handle_agent_callback(payload)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "event": event})
                return

            if parsed.path not in {"/run-batch", "/api/tasks"}:
                self.send_error(404)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body.decode("utf-8"))
                graphs = payload if isinstance(payload, list) else payload["graphs"]
                task_ids = manager.submit_batch(graphs)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "task_ids": task_ids}, status=201)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, body: Any, status: int = 200) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _health_payload(manager: Any, simulator_url: str) -> dict[str, Any]:
    runtime_info = (
        manager.runtime_info()
        if hasattr(manager, "runtime_info")
        else {"runtime_status": "unknown"}
    )
    ready = runtime_info.get("runtime_status") in {"ready", "running"}
    return {
        "ok": ready,
        "service": "execution-api",
        "simulator": simulator_url,
        "agent_service": os.environ.get(
            "AGENT_SERVICE_API_BASE",
            "http://127.0.0.1:8091",
        ),
        "runtime": runtime_info,
    }


def load_graph(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as graph_file:
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


def start_web_server(
    host: str,
    port: int,
    manager: Any,
    examples_dir: Path,
    simulator_url: str = "http://127.0.0.1:8767",
) -> ThreadingHTTPServer:
    # sandbox_service.py 只负责组装依赖；HTTP handler 的创建留在本模块内。
    return ThreadingHTTPServer(
        (host, port),
        make_handler(manager, examples_dir, simulator_url),
    )

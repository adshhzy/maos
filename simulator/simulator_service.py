import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from simulator.simulator_backend import get_job_status, start_simulated_job


DEFAULT_SIMULATOR_HOST = "127.0.0.1"
DEFAULT_SIMULATOR_PORT = 8767


def start_simulator_server(
    host: str = DEFAULT_SIMULATOR_HOST,
    port: int = DEFAULT_SIMULATOR_PORT,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/simulator/health":
                self._send_json({"ok": True})
                return
            if parsed.path.startswith("/api/simulator/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                try:
                    self._send_json(get_job_status(job_id))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/simulator/jobs":
                self.send_error(404)
                return
            try:
                payload = self._read_json()
                job = start_simulated_job(
                    node=payload["node"],
                    dependency_results=payload["dependency_results"],
                    graph_input=payload.get("graph_input", {}),
                    callback=payload.get("callback"),
                    job_id=payload.get("job_id"),
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "job": job}, status=201)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            return json.loads(raw_body.decode("utf-8"))

        def _send_json(self, body: Any, status: int = 200) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def main() -> None:
    server = ThreadingHTTPServer(
        (DEFAULT_SIMULATOR_HOST, DEFAULT_SIMULATOR_PORT),
        make_handler(),
    )
    print(
        "Simulator microservice running at "
        f"http://{DEFAULT_SIMULATOR_HOST}:{DEFAULT_SIMULATOR_PORT}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

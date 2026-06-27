import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from web.web_ui import INDEX_HTML


API_PROXY_PATHS = {
    "/state",
    "/examples",
    "/example",
    "/agent-trace",
    "/agent-input",
    "/run-batch",
}


def make_web_ui_handler(
    *,
    sandbox_api_base: str,
    static_dir: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Serve the dashboard and proxy API calls to the sandbox API service."""

    static_root = static_dir or Path(__file__).resolve().parent / "static"
    api_base = sandbox_api_base.rstrip("/") + "/"

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(INDEX_HTML.encode("utf-8"))))
                self.end_headers()
                return
            if _should_proxy(parsed.path):
                self._proxy()
                return
            self.send_error(404)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if _should_proxy(parsed.path):
                self._proxy()
                return
            if parsed.path in {"/", "/index.html"}:
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/config.js":
                self._send_config()
                return
            if parsed.path.startswith("/static/"):
                self._send_static(parsed.path.removeprefix("/static/"))
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if _should_proxy(parsed.path):
                self._proxy()
                return
            self.send_error(404)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_config(self) -> None:
            body = "window.MAOS_CONFIG = " + json.dumps(
                {"sandboxApiBase": sandbox_api_base.rstrip("/")},
                ensure_ascii=False,
            ) + ";\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_static(self, relative_path: str) -> None:
            target = (static_root / relative_path).resolve()
            if static_root.resolve() not in target.parents or not target.is_file():
                self.send_error(404)
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _content_type(target))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _proxy(self) -> None:
            url = urljoin(api_base, self.path.lstrip("/"))
            body = None
            if self.command in {"POST", "PUT", "PATCH"}:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
            headers = {}
            if self.headers.get("Content-Type"):
                headers["Content-Type"] = self.headers["Content-Type"]
            request = Request(url, data=body, headers=headers, method=self.command)
            try:
                with urlopen(request, timeout=120) as response:
                    payload = response.read()
                    self.send_response(response.status)
                    self._copy_response_headers(response.headers.items())
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
            except HTTPError as exc:
                payload = exc.read()
                self.send_response(exc.code)
                self._copy_response_headers(exc.headers.items())
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except URLError as exc:
                self._send_proxy_error(str(exc))

        def _copy_response_headers(self, headers: Any) -> None:
            skipped = {"connection", "transfer-encoding", "content-length", "server", "date"}
            for key, value in headers:
                if key.lower() not in skipped:
                    self.send_header(key, value)

        def _send_proxy_error(self, detail: str) -> None:
            payload = json.dumps(
                {"ok": False, "error": f"Sandbox API proxy failed: {detail}"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _should_proxy(path: str) -> bool:
    return path.startswith("/api/") or path in API_PROXY_PATHS


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def start_web_ui_server(
    host: str,
    port: int,
    *,
    sandbox_api_base: str,
    static_dir: Path | None = None,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port),
        make_web_ui_handler(sandbox_api_base=sandbox_api_base, static_dir=static_dir),
    )

"""HTTP client helpers for the Agent Service facade."""

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def agent_service_base() -> str:
    return os.environ.get("AGENT_SERVICE_API_BASE", "http://127.0.0.1:8091").rstrip("/")

def agent_service_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{agent_service_base()}{path}{query}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AgentService returned HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"AgentService is unavailable: {exc.reason}") from exc

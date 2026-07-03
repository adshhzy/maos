"""Local durable artifact store for A2A dependency transfer.

The current implementation stores JSON artifacts under ``MAOS_DATA_DIR``.  The
public API is intentionally small so it can later be backed by S3/MinIO without
changing the A2A message shape.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from maos_runtime.persistence import persist_artifact_record


ARTIFACT_STORE_VERSION = "maos-artifact-v1"
DEFAULT_MIME_TYPE = "application/json"


def store_dependency_artifact(
    content: dict[str, Any],
    *,
    source_artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    summary: str | None = None,
    mime_type: str = DEFAULT_MIME_TYPE,
) -> dict[str, Any]:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    content_digest = hashlib.sha256(encoded).hexdigest()
    scoped_digest_payload = {
        "content_hash": f"sha256:{content_digest}",
        "scope": _artifact_scope(metadata or {}),
        "source_artifact_id": source_artifact_id,
    }
    scoped_encoded = json.dumps(
        scoped_digest_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(scoped_encoded).hexdigest()
    artifact_ref = f"sha256-{digest}"
    record = {
        "version": ARTIFACT_STORE_VERSION,
        "artifact_ref": artifact_ref,
        "uri": _artifact_content_url(artifact_ref),
        "internal_uri": f"maos-artifact://{artifact_ref}",
        "content_hash": f"sha256:{content_digest}",
        "scope_hash": f"sha256:{digest}",
        "mime_type": mime_type,
        "size": len(encoded),
        "summary": summary or _summary_from_content(content),
        "source_artifact_id": source_artifact_id,
        "metadata": metadata or {},
        "content": content,
    }
    path = _artifact_path(artifact_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        persist_artifact_record(record)
    except Exception:
        pass
    return _public_record(record, include_content=False)


def load_artifact(artifact_ref: str, *, include_content: bool = False) -> dict[str, Any]:
    path = _artifact_path(artifact_ref)
    if not path.is_file():
        raise FileNotFoundError(artifact_ref)
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    return _public_record(record, include_content=include_content)


def load_artifact_content(artifact_ref: str) -> Any:
    return load_artifact(artifact_ref, include_content=True).get("content")


def artifact_store_dir() -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return base / "artifacts"


def _artifact_path(artifact_ref: str) -> Path:
    safe_ref = _safe_artifact_ref(artifact_ref)
    digest = safe_ref.removeprefix("sha256-")
    return artifact_store_dir() / digest[:2] / f"{safe_ref}.json"


def _safe_artifact_ref(artifact_ref: str) -> str:
    if not artifact_ref.startswith("sha256-"):
        raise ValueError(f"Unsupported artifact ref: {artifact_ref}")
    digest = artifact_ref.removeprefix("sha256-")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"Invalid artifact ref: {artifact_ref}")
    return artifact_ref


def _artifact_content_url(artifact_ref: str) -> str:
    base = os.environ.get("SANDBOX_API_BASE", "http://127.0.0.1:8766").rstrip("/")
    return f"{base}/api/artifacts/{artifact_ref}/content"


def _summary_from_content(content: dict[str, Any]) -> str:
    payload = content.get("payload") if isinstance(content, dict) else None
    if isinstance(payload, dict):
        for key in ("summary", "latest_comment", "stdout", "result", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _clip_summary(value)
    return _clip_summary(json.dumps(content, ensure_ascii=False, sort_keys=True))


def _artifact_scope(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return fields that isolate artifact refs between workflow executions."""

    scope_keys = (
        "producer_workflow_id",
        "consumer_workflow_id",
        "consumer_node_id",
        "workflow_id",
        "workflowId",
        "nodeId",
        "a2aTaskId",
        "sourceTaskId",
    )
    return {
        key: metadata.get(key)
        for key in scope_keys
        if metadata.get(key) not in (None, "")
    }


def _clip_summary(value: str, limit: int = 600) -> str:
    text = value.strip()
    return text if len(text) <= limit else text[:limit] + "...<summary-truncated>"


def _public_record(record: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    keys = (
        "version",
        "artifact_ref",
        "uri",
        "internal_uri",
        "content_hash",
        "scope_hash",
        "mime_type",
        "size",
        "summary",
        "source_artifact_id",
        "metadata",
    )
    public = {key: record.get(key) for key in keys if key in record}
    if include_content:
        public["content"] = record.get("content")
    return public

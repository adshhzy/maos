"""Agent output parsing helpers."""

import json
import re
from typing import Any


def _extract_structured_agent_output(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    json_object = _json_object_from_text(text)
    if json_object:
        return _normalize_agent_decision_payload(json_object)
    return _extract_decision_from_text(text)


def _json_object_from_text(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    candidates = _json_candidates_from_text(text)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _json_candidates_from_text(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)\s*```", text, re.DOTALL):
        fenced = match.group(1).strip()
        if fenced:
            candidates.extend(_balanced_json_objects(fenced))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    candidates.extend(_balanced_json_objects(stripped))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1].strip())
                start = None
    return objects


def _normalize_agent_decision_payload(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    decision = (
        normalized.get("decision")
        or normalized.get("route")
        or normalized.get("next_step")
        or normalized.get("下一步")
        or normalized.get("结论")
        or normalized.get("决定")
    )
    if decision is not None:
        normalized["decision"] = _normalize_decision_value(decision)
    return normalized


def _extract_decision_from_text(text: str) -> dict[str, Any]:
    lower = text.lower()
    if "needs_revision" in lower or "need_revision" in lower or "requires_revision" in lower:
        return {"decision": "needs_revision"}
    if "approved" in lower or "approve" in lower:
        return {"decision": "approved"}
    if "需要补充" in text or "需要修改" in text or "需要返工" in text or "不通过" in text:
        return {"decision": "needs_revision"}
    if "通过" in text or "批准" in text or "同意进入" in text:
        return {"decision": "approved"}
    return {}


def _normalize_decision_value(value: Any) -> str:
    text = str(value).strip().lower()
    text = text.replace("-", "_").replace(" ", "_")
    mapping = {
        "approve": "approved",
        "approved": "approved",
        "pass": "approved",
        "passed": "approved",
        "go": "approved",
        "通过": "approved",
        "批准": "approved",
        "同意": "approved",
        "needs_revision": "needs_revision",
        "need_revision": "needs_revision",
        "requires_revision": "needs_revision",
        "revise": "needs_revision",
        "revision": "needs_revision",
        "retry": "needs_revision",
        "需要补充": "needs_revision",
        "需要修改": "needs_revision",
        "需要返工": "needs_revision",
        "不通过": "needs_revision",
    }
    return mapping.get(text, text)


def _text_from_record(record: dict[str, Any]) -> str | None:
    for key in ("content", "text", "body", "message", "summary", "answer"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = record.get("parts")
    if isinstance(value, list):
        pieces = []
        for part in value:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
        if pieces:
            return "\n".join(pieces)
    return None


def _truncate_text(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return None if value is None else str(value)

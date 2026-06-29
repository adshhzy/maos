"""Agent output parsing helpers."""

import json
import re
from typing import Any


def _extract_structured_agent_output(
    text: str | None,
    *,
    allow_text_decision: bool = True,
    require_complete_decision: bool = False,
    required_fields: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not text:
        return {}
    json_object = _json_object_from_text(text)
    if json_object:
        normalized = _normalize_agent_decision_payload(json_object)
        if require_complete_decision and not _has_complete_decision_payload(normalized):
            return {}
        if required_fields and not _has_required_fields(normalized, required_fields):
            return {}
        return normalized
    if not allow_text_decision:
        return {}
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
    if "approved_with_risk" in lower or "approved with risk" in lower:
        return {"decision": "approved_with_risk"}
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
        "approved_with_risk": "approved_with_risk",
        "risk_approved": "approved_with_risk",
        "risk_pass": "approved_with_risk",
        "pass_with_risk": "approved_with_risk",
        "带风险通过": "approved_with_risk",
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


def _has_complete_decision_payload(value: dict[str, Any]) -> bool:
    decision = value.get("decision")
    if not isinstance(decision, str) or not decision:
        return False
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False
    if not isinstance(value.get("required_changes"), list):
        return False
    if decision == "approved_with_risk" and not isinstance(value.get("risks"), list):
        return False
    return True


def _has_required_fields(value: dict[str, Any], required_fields: list[str] | tuple[str, ...]) -> bool:
    for field in required_fields:
        current: Any = value
        for part in str(field).split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if current is None:
            return False
    return True


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

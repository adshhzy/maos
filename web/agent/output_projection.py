"""Extract final Agent output from comments."""

from typing import Any

from web.agent.normalization import parse_time


def select_final_output(comments: list[dict[str, Any]]) -> dict[str, Any]:
    if not comments:
        return {}
    agent_comments = [
        comment
        for comment in comments
        if str(comment.get("author_type") or "").lower() == "agent"
        and isinstance(comment.get("content"), str)
        and comment.get("content")
    ]
    candidates = agent_comments or [
        comment
        for comment in comments
        if isinstance(comment.get("content"), str) and comment.get("content")
    ]
    if not candidates:
        return {}
    comment = max(
        candidates,
        key=lambda item: parse_time(item.get("updated_at") or item.get("created_at")),
    )
    content = str(comment.get("content") or "")
    return {
        "id": comment.get("id"),
        "author_type": comment.get("author_type"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "chars": len(content),
        "approx_tokens": max(0, (len(content) + 3) // 4),
        "content": content,
    }

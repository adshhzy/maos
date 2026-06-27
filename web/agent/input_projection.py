"""Extract Web-displayable Agent input from task descriptions."""

import json
import re
from typing import Any


def extract_agent_input(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    description = str(task.get("description") or "")
    context_policy = first_markdown_section(description, ["上下文策略", "Context Policy"])
    node_instruction = first_markdown_section(
        description,
        ["任务指令（必须执行）", "任务指令", "Node Instruction"],
    )
    graph_input_text = first_fenced_json_after_heading(
        description,
        ["原始业务输入", "Original Business Input", "Graph Input"],
    )
    upstream_results_text = first_fenced_json_after_heading(
        description,
        ["上游节点结果", "Upstream Node Results", "Dependency Results"],
    )
    context_payload_text = extract_fenced_json_after_heading(description, "A2A Context Payload")
    context_payload: Any = None
    if context_payload_text:
        try:
            context_payload = json.loads(context_payload_text)
        except Exception:
            context_payload = None
    graph_input = _loads_json_object(graph_input_text)
    upstream_results = _loads_json_object(upstream_results_text)
    dependency_results = (
        upstream_results
        if isinstance(upstream_results, dict)
        else context_payload.get("dependency_results", {})
        if isinstance(context_payload, dict)
        else {}
    )
    graph_input_value = (
        graph_input
        if isinstance(graph_input, dict)
        else context_payload.get("graph_input", {})
        if isinstance(context_payload, dict)
        else {}
    )
    return {
        "description_chars": len(description),
        "description": description,
        "context_policy": context_policy,
        "node_instruction": node_instruction,
        "original_business_input": graph_input_text,
        "upstream_node_results": upstream_results_text,
        "a2a_context_payload": context_payload_text,
        "dependency_node_ids": sorted(dependency_results) if isinstance(dependency_results, dict) else [],
        "graph_input_keys": (
            sorted(graph_input_value)
            if isinstance(graph_input_value, dict)
            else []
        ),
    }

def first_markdown_section(description: str, headings: list[str]) -> str:
    for heading in headings:
        section = extract_markdown_section(description, heading)
        if section:
            return section
    return ""

def first_fenced_json_after_heading(description: str, headings: list[str]) -> str:
    for heading in headings:
        section = extract_fenced_json_after_heading(description, heading)
        if section:
            return section
    return ""

def extract_markdown_section(description: str, heading: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(heading)}\s*$\s*(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, description)
    return match.group(1).strip() if match else ""

def extract_fenced_json_after_heading(description: str, heading: str) -> str:
    section = extract_markdown_section(description, heading)
    match = re.search(r"(?ms)```json\s*(.*?)\s*```", section)
    if match:
        return match.group(1).strip()
    return section.strip()

def _loads_json_object(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None

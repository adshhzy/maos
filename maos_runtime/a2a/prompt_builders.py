"""Prompt, description, and metadata builders for Agent runtime calls."""

from typing import Any

from maos_runtime.a2a_constants import MULTICA_BACKEND, PROVIDED_CONTEXT_ONLY
from maos_runtime.a2a.runtime_config_helpers import (
    _allowed_skill_slugs,
    _hermes_context_policy,
    _hermes_prompt_limit,
    _include_payload_metadata,
    _maos_compact_bootstrap_enabled,
    _multica_context_policy,
    _multica_description_limit,
    _multica_execution_mode,
    _multica_requested_agent_name,
    _multica_runtime_profile,
    _node_agent_config,
    _payload_metadata_limit,
    _safe_json,
)


def _multica_title(node: dict[str, Any], workflow_id: str) -> str:
    title = _node_agent_config(node).get("title") or node.get("title") or node.get("label") or node["id"]
    return f"[MAOS:{workflow_id}] {title}"


def _artifact_api_instruction() -> str:
    return (
        "## URI / Artifact Access\n\n"
        "如果上游节点结果里包含 artifact_ref / uri / content_hash / mime_type / size / summary，"
        "请把 uri 视为本任务允许访问的 A2A artifact API，通过只读 HTTP GET 拉取完整上游内容。"
        "如果任务需要核验公开资料，也允许访问任务相关的公开 http/https URI。"
        "允许使用 curl、wget 或 WebFetch 读取 URI；不要对远程服务执行写入、登录、提交表单或破坏性操作。"
        "如果当前 Agent runtime 无法访问该 API，请基于 summary 明确说明信息不完整，"
        "或要求上游切换 inline 模式。\n\n"
    )

def _multica_description(
    node: dict[str, Any],
    dependency_results: dict[str, dict[str, Any]],
    graph_input: dict[str, Any],
    workflow_id: str,
    a2a_task_id: str,
) -> str:
    context_policy = _multica_context_policy(node)
    runtime_profile = _multica_runtime_profile(node)
    instruction = (
        _node_agent_config(node).get("prompt")
        or node.get("prompt")
        or node.get("description")
        or "Execute this control-flow node and return the result as a concise comment."
    )
    policy_text = _context_policy_instructions(context_policy, runtime_profile)
    dependency_context = _hermes_dependency_context(dependency_results)
    description_limit = _multica_description_limit(node)
    return (
        "# MAOS 业务子任务\n\n"
        "你正在执行一个持久化多智能体编排图中的业务子任务。请把“任务指令”当作唯一主任务，"
        "结合“原始业务输入”和“上游节点结果”直接完成工作。完成后，请把最终结果作为简洁评论提交，"
        "并将 Multica 任务状态更新为 in_review 或 done。\n\n"
        "## 上下文策略\n\n"
        f"{policy_text}\n\n"
        "## 任务指令（必须执行）\n\n"
        f"{instruction}\n\n"
        "## 原始业务输入\n\n"
        "```json\n"
        f"{_safe_json(graph_input, description_limit)}\n"
        "```\n\n"
        "## 上游节点结果\n\n"
        "```json\n"
        f"{_safe_json(dependency_context, description_limit)}\n"
        "```\n\n"
        f"{_artifact_api_instruction()}"
        "## 执行边界\n\n"
        "- 只完成上面“任务指令”描述的业务任务。\n"
        "- 不要解释编排协议，不要把输入 JSON 当成需要分析的协议对象。\n"
        "- 不要创建新的 MAOS 任务、工作流、issue、脚本、测试文件或本地 API 请求。\n"
        "- 不要读取仓库、历史评论、外部文档或网页，除非任务指令明确要求。\n"
    )

def _context_policy_instructions(context_policy: str, runtime_profile: str) -> str:
    if context_policy == PROVIDED_CONTEXT_ONLY:
        return (
            f"Policy: {context_policy}; runtime profile: {runtime_profile}. "
            "Use only the task instruction, original business input, and upstream node results in this "
            "description. Do not inspect repositories, workspace files, previous runs, comments, "
            "metadata, external documents, or web pages. Do not use tool-heavy coding-agent capabilities "
            "unless the task instruction explicitly requires repository work."
        )
    return (
        f"Policy: {context_policy}; runtime profile: {runtime_profile}. "
        "Use the task instruction, original business input, and upstream node results in this "
        "description as the authoritative task input. Read external workspace or repository context "
        "only when the task instruction explicitly requires it."
    )

def _node_context_payload(node: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "id",
        "label",
        "type",
        "operation",
        "deps",
        "join",
        "max_visits",
        "params",
        "timeout_seconds",
    ):
        if key in node:
            payload[key] = node[key]
    agent = _node_agent_config(node)
    if agent:
        payload["agent"] = {
            key: agent[key]
            for key in (
                "backend",
                "agent_key",
                "agent_id",
                "agent_name",
                "context_policy",
                "runtime_profile",
                "execution_profile",
                "execution_mode",
                "artifact_transfer_mode",
                "dependency_artifact_mode",
            )
            if key in agent
        }
    return payload

def _hermes_task_context(node: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "id",
        "label",
        "type",
        "operation",
        "deps",
        "join",
        "max_visits",
        "params",
    ):
        if key in node:
            payload[key] = node[key]
    return payload

def _hermes_expected_output_hint(instruction: str) -> dict[str, Any]:
    lower = instruction.lower()
    wants_markdown_or_text = any(
        marker in lower
        for marker in (
            "markdown",
            "不要输出json",
            "不要输出 json",
            "not json",
            "no json",
            "report",
        )
    ) or any(
        marker in instruction
        for marker in (
            "报告",
            "正文",
            "结构化文本",
            "中文输出",
            "不要输出JSON",
            "不要输出 JSON",
        )
    )
    wants_json = (
        not wants_markdown_or_text
        and (
            any(
                marker in lower
                for marker in (
                    "output json",
                    "return json",
                    "valid json",
                    "json object",
                    "json_object",
                )
            )
            or any(
                marker in instruction
                for marker in (
                    "输出JSON",
                    "输出 JSON",
                    "返回JSON",
                    "返回 JSON",
                    "合法JSON",
                    "合法 JSON",
                    "JSON对象",
                    "JSON 对象",
                    "字段包含",
                    "字段包括",
                )
            )
        )
    )
    return {
        "format": "json_object" if wants_json else "structured_chinese_text",
        "do_not_ask_clarifying_questions": True,
        "continue_with_reasonable_assumptions": True,
    }

def _hermes_dependency_context(
    dependency_results: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for node_id, result in dependency_results.items():
        payload = result.get("payload", {}) if isinstance(result, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        useful_payload = {}
        for key in (
            "structured_output",
            "decision",
            "reason",
            "status",
            "risk_level",
            "error",
            "failure_reason",
        ):
            if key in payload:
                useful_payload[key] = _compact_dependency_prompt_value(payload[key])
        summary = payload.get("latest_comment") or payload.get("stdout") or payload.get("summary")
        if summary:
            useful_payload["summary"] = _compact_dependency_prompt_value(summary)
        for key, value in payload.items():
            if key.startswith("hermes_") or key in {
                "trace",
                "comments",
                "runs",
                "messages",
                "latest_comment",
                "stdout",
                "summary",
                "agent_backend",
                "agent_key",
                "requested_agent_key",
                "context_policy",
                "runtime_profile",
                "execution_mode",
                "agent_service_task_id",
                "title",
            }:
                continue
            if key not in useful_payload:
                useful_payload[key] = _compact_dependency_prompt_value(value)
        compact[node_id] = {
            "node": result.get("node", node_id) if isinstance(result, dict) else node_id,
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "duration_seconds": result.get("duration_seconds") if isinstance(result, dict) else None,
            "payload": useful_payload,
        }
    return compact


def _compact_dependency_prompt_value(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_compact_dependency_prompt_value(item) for item in value[-10:]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "raw",
                "history",
                "full_text",
                "transcript",
                "dependency_artifacts",
                "messages",
                "comments",
                "runs",
                "trace",
            }:
                continue
            compact[key_text] = _compact_dependency_prompt_value(item)
        return compact
    return value

def _multica_metadata(
    node: dict[str, Any],
    workflow_id: str,
    task_id: str,
    context_id: str,
    dependency_results: dict[str, dict[str, Any]],
    graph_input: dict[str, Any],
    context_policy: str,
    runtime_profile: str,
    execution_mode: str,
    dispatch_agent_key: str | None,
    requested_agent_key: Any,
    idempotency_key: str | None,
) -> dict[str, str | int | float | bool]:
    agent = _node_agent_config(node)
    dependency_json = _safe_json(dependency_results)
    graph_input_json = _safe_json(graph_input)
    metadata: dict[str, str | int | float | bool] = {
        "maos_task": True,
        "maos_compact_bootstrap": _maos_compact_bootstrap_enabled(agent, node, runtime_profile),
        "maos_backend": MULTICA_BACKEND,
        "execution_mode": execution_mode,
        "runtime_profile": runtime_profile,
        "context_policy": context_policy,
        "comment_history_policy": str(
            agent.get("comment_history_policy")
            or node.get("comment_history_policy")
            or "disabled"
        ),
        "metadata_policy": str(
            agent.get("metadata_policy")
            or node.get("metadata_policy")
            or "disabled"
        ),
        "skill_loading_policy": str(
            agent.get("skill_loading_policy")
            or node.get("skill_loading_policy")
            or "none"
        ),
        "maos_allowed_skill_slugs": _allowed_skill_slugs(agent, node),
        "tool_policy": str(agent.get("tool_policy", "no_external_tools")),
        "workflow_id": workflow_id,
        "node_id": node["id"],
        "a2a_task_id": task_id,
        "idempotency_key": idempotency_key or "",
        "context_id": context_id,
        "operation": str(node.get("operation", "agent_task")),
        "dispatch_agent_key": dispatch_agent_key or "",
        "requested_agent_key": "" if requested_agent_key is None else str(requested_agent_key),
        "requested_agent_name": _multica_requested_agent_name(node) or "",
        "dependency_node_ids": ",".join(sorted(dependency_results)),
        "dependency_result_bytes": len(dependency_json.encode("utf-8")),
        "graph_input_bytes": len(graph_input_json.encode("utf-8")),
    }
    if _include_payload_metadata(node):
        metadata["dependency_results_json"] = _safe_json(
            dependency_results,
            _payload_metadata_limit(node),
        )
        metadata["graph_input_json"] = _safe_json(graph_input, _payload_metadata_limit(node))
    return metadata

def _hermes_prompt(
    node: dict[str, Any],
    dependency_results: dict[str, dict[str, Any]],
    graph_input: dict[str, Any],
    workflow_id: str,
    a2a_task_id: str,
    context_policy: str,
    runtime_profile: str,
) -> str:
    instruction = (
        _node_agent_config(node).get("prompt")
        or node.get("prompt")
        or node.get("description")
        or "请完成当前业务子任务，并返回最终结果。"
    )
    task = {
        **_hermes_task_context(node),
        "instruction": instruction,
        "expected_output": _hermes_expected_output_hint(instruction),
    }
    dependency_context = _hermes_dependency_context(dependency_results)
    prompt_limit = _hermes_prompt_limit(node)
    output_hint = task["expected_output"]["format"]
    return (
        "# 请完成这个业务子任务\n\n"
        "你现在收到的是一个工作流节点任务。请把下面的“任务指令”当作唯一主任务并直接完成，"
        "不要解释编排协议，不要询问任务是什么，不要要求用户补充图片或文件。\n"
        "如果信息不足，请基于已给输入做合理假设，并在最终结果里说明假设。\n\n"
        "## 任务指令（必须执行）\n\n"
        f"{instruction}\n\n"
        "## 原始业务输入\n\n"
        "```json\n"
        f"{_safe_json(graph_input, prompt_limit)}\n"
        "```\n\n"
        "## 上游节点结果\n\n"
        "```json\n"
        f"{_safe_json(dependency_context, prompt_limit)}\n"
        "```\n\n"
        f"{_artifact_api_instruction()}"
        "## 执行边界\n\n"
        "- 只完成上面“任务指令”描述的业务任务。\n"
        "- 不要创建新的任务、工作流、issue、脚本、测试文件或本地 API 请求。\n"
        "- 不要调用 http://127.0.0.1:8765、http://127.0.0.1:8767、http://127.0.0.1:8091。\n"
        "- 不要运行无关终端命令，不要读写仓库文件；允许为了完成任务只读访问相关 http/https URI，包括 Artifact API、公开网页和公开文档。\n\n"
        "## 最终输出\n\n"
        f"- 期望输出格式：{output_hint}。\n"
        "- 如果任务指令要求 JSON，请只输出一个合法 JSON 对象，不要附加 markdown 代码块或解释。\n"
        "- 如果任务指令没有要求 JSON，请用中文输出结构化结果。\n"
        "- 不要输出对执行过程、工具、运行时或编排系统的说明。\n"
    )

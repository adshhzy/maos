"""AgentService 查询与 trace 转换逻辑。

这些函数只负责把 Multica/AgentService 的任务、评论和运行消息整理成
Web 看板需要的输入、最终输出和可读执行链路。
"""

import json
import os
import re
from datetime import datetime
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


def build_agent_trace(task_id: str) -> dict[str, Any]:
    task_response = agent_service_get(f"/tasks/{task_id}")
    runs_response = agent_service_get(f"/tasks/{task_id}/runs")
    comments_response = agent_service_get(f"/tasks/{task_id}/comments", {"recent": 20})
    task = unwrap_data(task_response)
    runs = response_items(runs_response)
    comments = response_items(comments_response)
    latest_run = select_latest_run(runs)
    messages: list[dict[str, Any]] = []
    if latest_run.get("id"):
        messages_response = agent_service_get(
            f"/runs/{latest_run['id']}/messages",
            {"issue_id": task_id},
        )
        messages = response_items(messages_response)
    steps = build_trace_steps(messages)
    return {
        "ok": True,
        "agent_service": agent_service_base(),
        "task": compact_task(task),
        "agent_input": extract_agent_input(task),
        "run": compact_run(latest_run),
        "final_output": select_final_output(comments),
        "summary": summarize_trace(steps),
        "timeline": build_trace_timeline(steps),
        "steps": steps,
    }


def build_agent_input(task_id: str) -> dict[str, Any]:
    task_response = agent_service_get(f"/tasks/{task_id}")
    task = unwrap_data(task_response)
    return {
        "ok": True,
        "agent_service": agent_service_base(),
        "task": compact_task(task),
        "agent_input": extract_agent_input(task),
    }


def unwrap_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def response_items(value: Any) -> list[dict[str, Any]]:
    data = unwrap_data(value)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "messages", "runs", "data"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [data]
    return []


def select_latest_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {}
    time_keys = ("completed_at", "ended_at", "started_at", "created_at", "updated_at")
    return max(runs, key=lambda run: max(parse_time(run.get(key)) for key in time_keys))


def compact_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    return {
        "id": task.get("id"),
        "title": task.get("title") or task.get("name"),
        "status": task.get("status") or task.get("state"),
        "assignee": task.get("assignee") or task.get("agent"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }


def extract_agent_input(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    description = str(task.get("description") or "")
    context_payload_text = extract_fenced_json_after_heading(description, "A2A Context Payload")
    context_payload: Any = None
    if context_payload_text:
        try:
            context_payload = json.loads(context_payload_text)
        except Exception:
            context_payload = None
    dependency_results = (
        context_payload.get("dependency_results", {})
        if isinstance(context_payload, dict)
        else {}
    )
    return {
        "description_chars": len(description),
        "description": trim_text(description, 20000),
        "context_policy": trim_text(extract_markdown_section(description, "Context Policy"), 4000),
        "node_instruction": trim_text(extract_markdown_section(description, "Node Instruction"), 8000),
        "a2a_context_payload": trim_text(context_payload_text, 20000),
        "dependency_node_ids": sorted(dependency_results) if isinstance(dependency_results, dict) else [],
        "graph_input_keys": (
            sorted(context_payload.get("graph_input", {}))
            if isinstance(context_payload, dict) and isinstance(context_payload.get("graph_input"), dict)
            else []
        ),
    }


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


def compact_run(run: dict[str, Any]) -> dict[str, Any]:
    if not run:
        return {}
    return {
        "id": run.get("id"),
        "status": run.get("status") or run.get("state"),
        "agent": run.get("agent") or run.get("agent_name"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at") or run.get("ended_at"),
    }


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
        "content": trim_text(content, 12000),
    }


def build_trace_steps(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(messages, key=lambda message: int(message.get("seq") or 0))
    steps = []
    for message in ordered:
        text = message_text(message)
        kind = message_kind(message)
        created_at = (
            message.get("created_at")
            or message.get("timestamp")
            or message.get("time")
            or message.get("ts")
        )
        step = {
            "seq": message.get("seq"),
            "type": message.get("type") or kind,
            "kind": kind,
            "tool": message.get("tool") or message.get("name") or "",
            "status": message.get("status") or message.get("state") or "",
            "created_at": created_at,
            "chars": len(text),
            "approx_tokens": max(0, (len(text) + 3) // 4),
            "preview": trim_text(text, 2400),
        }
        exit_code = message.get("exit_code") or message.get("return_code")
        if exit_code is not None:
            step["exit_code"] = exit_code
        steps.append(step)
    return steps


def build_trace_timeline(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    text_buffer: list[dict[str, Any]] = []

    def flush_text() -> None:
        nonlocal text_buffer
        if not text_buffer:
            return
        combined = "".join(str(step.get("preview") or "") for step in text_buffer).strip()
        if combined:
            timeline.append(
                {
                    "kind": "agent_text",
                    "title": classify_agent_text(combined),
                    "description": describe_agent_text(combined),
                    "start_seq": text_buffer[0].get("seq"),
                    "end_seq": text_buffer[-1].get("seq"),
                    "created_at": text_buffer[0].get("created_at"),
                    "chars": sum(int(step.get("chars") or 0) for step in text_buffer),
                    "approx_tokens": sum(int(step.get("approx_tokens") or 0) for step in text_buffer),
                    "preview": trim_text(combined, 2600),
                }
            )
        text_buffer = []

    for step in steps:
        step_type = str(step.get("type") or "")
        if step_type == "text":
            text_buffer.append(step)
            continue
        flush_text()
        timeline.append(describe_tool_step(step))
    flush_text()
    timeline = refine_timeline_with_neighbors(timeline)
    return add_timeline_durations(timeline)


def refine_timeline_with_neighbors(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, item in enumerate(timeline):
        if item.get("kind") != "agent_text":
            continue
        next_item = timeline[index + 1] if index + 1 < len(timeline) else None
        previous_item = timeline[index - 1] if index > 0 else None
        refined = refine_agent_text_from_next_step(item, next_item, previous_item)
        if refined:
            item.update(refined)
    return timeline


def refine_agent_text_from_next_step(
    item: dict[str, Any],
    next_item: dict[str, Any] | None,
    previous_item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not next_item:
        return None
    next_title = str(next_item.get("title") or "")
    next_preview = str(next_item.get("preview") or "").lower()
    next_tool = str(next_item.get("tool") or "").lower()
    previous_title = str((previous_item or {}).get("title") or "")
    current_title = str(item.get("title") or "")

    if next_tool == "python":
        if "json" in next_preview or "payload" in next_preview:
            return inferred_trace_label(
                "生成解析 A2A 输入的临时 Python 代码",
                "下一步是 Python 解析脚本，因此这段空档大概率是在模型构造临时代码，用来解析 issue 描述、提取 A2A JSON 或整理上游结果。",
            )
        if "operation_playbook" in next_preview or "document" in next_preview:
            return inferred_trace_label(
                "准备临时 Python 脚本处理上游结果",
                "下一步是 Python tool call，脚本中出现文档/节点变量；这段空档更准确地说是在模型生成临时代码，用来提取上游结果、整理结构化内容，可能同时组装文档草稿。",
            )
        return inferred_trace_label(
            "准备生成并运行 Python 辅助脚本",
            "下一步是 Python tool call，因此这段空档大概率是在模型生成临时代码，用来解析上游输出、提取结果，或整理后续步骤需要的数据。",
        )

    if next_tool in {"terminal", "shell"}:
        if "cat >" in next_preview or "heredoc" in next_preview or "<< 'eof'" in next_preview:
            return inferred_trace_label(
                "生成 markdown 文件写入命令",
                "下一步是终端 heredoc 写文件，因此这段空档大概率是在模型生成较长 markdown 内容和对应 shell 命令。",
            )
        if "multica issue comment add" in next_preview:
            return inferred_trace_label(
                "准备提交最终结果评论",
                "下一步是 Multica 评论提交命令，因此这段空档大概率是在模型准备 comment add 的文件路径和 CLI 参数。",
            )
        if "multica issue update" in next_preview and "--status" in next_preview:
            return inferred_trace_label(
                "准备更新任务完成状态",
                "下一步是 Multica 状态更新命令，因此这段空档大概率是在模型确认结果已提交，并准备把任务置为 in_review/done。",
            )
        if "multica issue get" in next_preview:
            return inferred_trace_label(
                "准备读取任务输入",
                "下一步是读取 Multica issue，因此这段空档大概率是在模型决定先获取任务描述和 A2A 输入。",
            )
        if "pwd" in next_preview or "ls" in next_preview or "echo" in next_preview:
            return inferred_trace_label(
                "准备检查运行目录或文件写入能力",
                "下一步是终端环境检查命令，因此这段空档大概率是在模型排查工作目录、权限或文件写入方式。",
            )
        return inferred_trace_label(
            "准备生成终端命令",
            "下一步是 terminal tool call，因此这段空档大概率是在模型生成 shell 命令或整理命令参数。",
        )

    if "工具返回" in previous_title and "总结" in current_title:
        return inferred_trace_label(
            "根据工具结果生成完成总结",
            "前一步工具已经成功返回，因此这段内容是在模型总结本节点产出和执行结果。",
        )
    if "工具返回" in previous_title:
        return inferred_trace_label(
            "读取工具结果并决定下一步",
            "前一步是工具返回结果，因此这段空档大概率是在模型理解工具输出，并规划后续动作。",
        )
    return None


def inferred_trace_label(title: str, description: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "inferred_from_neighbor": True,
    }


def add_timeline_durations(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_ts: float | None = None
    for index, item in enumerate(timeline):
        current_ts = parse_time(item.get("created_at"))
        next_ts = (
            parse_time(timeline[index + 1].get("created_at"))
            if index + 1 < len(timeline)
            else 0.0
        )
        item["wait_since_previous_seconds"] = (
            round(current_ts - previous_ts, 2)
            if previous_ts is not None and current_ts > 0 and current_ts >= previous_ts
            else None
        )
        item["time_to_next_seconds"] = (
            round(next_ts - current_ts, 2)
            if current_ts > 0 and next_ts >= current_ts
            else None
        )
        if item["time_to_next_seconds"] is not None and item["time_to_next_seconds"] >= 30:
            item["duration_severity"] = "slow"
        elif item["time_to_next_seconds"] is not None and item["time_to_next_seconds"] >= 10:
            item["duration_severity"] = "medium"
        else:
            item["duration_severity"] = "normal"
        if current_ts > 0:
            previous_ts = current_ts
    return timeline


def classify_agent_text(text: str) -> str:
    normalized = text.lower()
    if "issue details" in normalized or "get the issue" in normalized:
        return "准备读取 Multica 任务详情"
    if "a2a context" in normalized or "dependency results" in normalized or "parse" in normalized:
        return "准备解析 A2A 上下文和上游结果"
    if "understand the task" in normalized or "i need to create" in normalized:
        return "理解任务目标并规划输出内容"
    if "write" in normalized and ("file" in normalized or "location" in normalized):
        return "准备写入中间文件"
    if "submit" in normalized and "comment" in normalized:
        return "准备把最终结果提交为 Multica 评论"
    if "update" in normalized and "status" in normalized:
        return "准备更新 Multica 任务状态"
    if "successfully completed" in normalized or "任务完成总结" in text or "summary" in normalized:
        return "总结已完成的工作"
    if "great" in normalized or "perfect" in normalized:
        return "确认上一步操作成功"
    return "Agent 生成中间思考或说明"


def describe_agent_text(text: str) -> str:
    title = classify_agent_text(text)
    if title == "准备读取 Multica 任务详情":
        return "Agent 决定先读取当前 Multica issue，获取节点指令和注入的 A2A 输入。"
    if title == "准备解析 A2A 上下文和上游结果":
        return "Agent 正在把 issue 描述里的 A2A Context Payload 解析出来，识别依赖节点输出和当前节点要求。"
    if title == "理解任务目标并规划输出内容":
        return "Agent 已读到上游上下文，开始规划本节点需要产出的结构、章节和关键内容。"
    if title == "准备写入中间文件":
        return "Agent 想把较长结果先写成文件，再通过 Multica CLI 作为评论内容提交。"
    if title == "准备把最终结果提交为 Multica 评论":
        return "Agent 已生成最终内容，准备调用 Multica 评论接口把结果写回任务。"
    if title == "准备更新 Multica 任务状态":
        return "Agent 已提交结果，准备把任务状态改为 in_review 或 done，通知编排层该节点可结束。"
    if title == "总结已完成的工作":
        return "Agent 对本次执行做最终总结，说明产出内容和已完成的提交动作。"
    if title == "确认上一步操作成功":
        return "Agent 看到工具返回成功，确认可以进入下一步。"
    return "这是 Agent 在两次工具调用之间输出的自然语言思考或过渡说明。"


def describe_tool_step(step: dict[str, Any]) -> dict[str, Any]:
    step_type = str(step.get("type") or "")
    tool = str(step.get("tool") or "")
    preview = str(step.get("preview") or "")
    if step_type == "tool_use":
        title, description = classify_tool_use(tool, preview)
    elif step_type == "tool_result":
        title, description = classify_tool_result(tool, preview)
    else:
        title, description = ("记录运行事件", "Agent runtime 记录了一条非文本、非工具调用的事件。")
    return {
        "kind": step_type or "message",
        "title": title,
        "description": description,
        "start_seq": step.get("seq"),
        "end_seq": step.get("seq"),
        "created_at": step.get("created_at"),
        "tool": tool,
        "chars": step.get("chars") or 0,
        "approx_tokens": step.get("approx_tokens") or 0,
        "preview": preview,
        "status": step.get("status") or "",
        "exit_code": step.get("exit_code"),
    }


def classify_tool_use(tool: str, preview: str) -> tuple[str, str]:
    lower = preview.lower()
    if "multica issue get" in lower:
        return ("调用工具读取任务详情", "Agent 调用 Multica CLI 获取当前 issue 的完整描述、元数据和 A2A Context。")
    if "multica issue comment add" in lower:
        return ("调用工具提交最终评论", "Agent 调用 Multica CLI，把生成好的最终结果作为评论写回当前任务。")
    if "multica issue update" in lower and "--status" in lower:
        return ("调用工具更新任务状态", "Agent 调用 Multica CLI，把任务状态更新为 in_review/done，供编排层轮询识别完成。")
    if tool == "python":
        if "json" in lower and ("parse" in lower or "payload" in lower):
            return ("运行 Python 解析 A2A 输入", "Agent 使用 Python 脚本解析 issue 描述中的 JSON 上下文，提取依赖结果。")
        if "operation_playbook" in lower or "document" in lower:
            return ("运行 Python 处理上游结果并组装草稿", "Agent 使用临时 Python 脚本提取上游结果、整理结构化内容；如果脚本中包含长文本变量，也可能同时组装文档草稿。")
        return ("运行 Python 辅助脚本", "Agent 使用 Python 做结构化处理、结果提取或临时数据整理。")
    if tool in {"terminal", "shell"}:
        if "cat >" in lower or "content-file" in lower:
            return ("运行终端写入结果文件", "Agent 把较长输出写入 markdown 文件，方便作为 Multica 评论提交。")
        if "pwd" in lower or "ls" in lower:
            return ("运行终端检查工作目录", "Agent 检查当前运行目录和可写环境，确认能否创建文件。")
        if "echo" in lower:
            return ("运行终端测试文件写入", "Agent 通过简单写文件命令验证当前工作目录是否可写。")
        return ("运行终端命令", "Agent 调用终端执行一个辅助操作。")
    return (f"调用工具 {tool or 'tool'}", "Agent 发起一次工具调用。")


def classify_tool_result(tool: str, preview: str) -> tuple[str, str]:
    lower = preview.lower()
    if "comment added" in lower:
        return ("工具返回：最终评论已提交", "Multica 返回评论创建成功，说明节点结果已经写回 issue。")
    if tool == "python" and ("document length" in lower or "created successfully" in lower):
        return ("工具返回：草稿内容已生成", "Python 输出显示已生成草稿内容或文档变量；这说明临时代码已经把结果整理成可提交文本。")
    if "exit_code:** 0" in lower or '"status"' in lower or "execution complete" in lower:
        if tool == "python":
            return ("工具返回：Python 脚本执行成功", "Python 辅助脚本已经完成，输出可供 Agent 继续处理。")
        return ("工具返回：命令执行成功", "终端或 Multica CLI 命令成功返回，Agent 可以进入下一步。")
    if "error" in lower or "traceback" in lower or "exit_code:** 1" in lower:
        return ("工具返回：执行遇到错误", "工具调用返回错误，Agent 随后可能会调整方案或重试。")
    return ("工具返回结果", "Agent 收到工具执行结果，并将基于该结果决定下一步。")


def message_kind(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "").lower()
    tool = str(message.get("tool") or message.get("name") or "").lower()
    if message_type == "tool_use":
        return f"tool_use:{tool or 'tool'}"
    if message_type == "tool_result":
        return f"tool_result:{tool or 'tool'}"
    if "terminal" in tool or "shell" in tool:
        return "terminal"
    if "python" in tool:
        return "python"
    if message_type:
        return message_type
    return "message"


def message_text(message: dict[str, Any]) -> str:
    for key in ("text", "content", "output", "body", "message"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    for key in ("input", "result", "payload"):
        value = message.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for nested_key in ("text", "content", "output", "command", "code"):
                nested = value.get(nested_key)
                if isinstance(nested, str):
                    return nested
    return json.dumps(message, ensure_ascii=False, default=str)


def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated>"


def summarize_trace(steps: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    first_at = ""
    last_at = ""
    for step in steps:
        step_type = str(step.get("type") or "message")
        type_counts[step_type] = type_counts.get(step_type, 0) + 1
        tool = str(step.get("tool") or "")
        if tool:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        created_at = str(step.get("created_at") or "")
        if created_at and (not first_at or parse_time(created_at) < parse_time(first_at)):
            first_at = created_at
        if created_at and (not last_at or parse_time(created_at) > parse_time(last_at)):
            last_at = created_at
    return {
        "message_count": len(steps),
        "type_counts": type_counts,
        "tool_counts": tool_counts,
        "visible_chars": sum(int(step.get("chars") or 0) for step in steps),
        "approx_visible_tokens": sum(int(step.get("approx_tokens") or 0) for step in steps),
        "first_at": first_at,
        "last_at": last_at,
    }


def parse_time(value: Any) -> float:
    if not value:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0



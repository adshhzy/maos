"""Build readable trace timelines and Chinese step descriptions."""

from typing import Any

from web.agent.normalization import parse_time, trim_text


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
        return "准备解析任务输入和上游结果"
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
    if title == "准备解析任务输入和上游结果":
        return "Agent 正在从 issue 描述里读取原始业务输入和上游节点结果，识别当前节点要求。"
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

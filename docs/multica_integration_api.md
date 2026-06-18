# 持久化执行层与 Multica 的当前接口 API

本文档总结当前代码中“持久化执行层 / Persistent Execution Sandbox”与 Multica daemon / Agent Service 之间的实际接口。它描述的是当前实现，不是未来完整蓝图。

## 1. 模块边界

### 持久化执行层

持久化执行层负责：

- 接收任务图 JSON，并启动 Temporal workflow。
- 根据图结构调度节点执行。
- 当节点 backend 为 `multica` 时，把节点转换为 Multica Task。
- 在 Temporal workflow 中持久等待 Multica 任务完成。
- 从 Multica 读取任务状态、评论、run 和可选 run messages。
- 把 Multica 输出转换为 A2A artifact，再作为节点结果传递给下游节点。

### Multica daemon / Agent Service

Multica 负责：

- 创建并管理真实 Agent Task。
- 根据任务描述和 metadata 调用具体 Agent runtime。
- 保存任务状态、评论、运行记录和消息链路。
- 当前版本由持久化执行层轮询其状态；暂未依赖 Multica 主动 push callback。

## 2. 配置项

| 配置项 | 默认值 | 作用 |
|---|---:|---|
| `AGENT_SERVICE_API_BASE` | `http://127.0.0.1:8091` | Multica Agent Service base URL。 |
| `AGENT_SERVICE_POLL_SECONDS` | `30` | 未在节点中配置 `poll_seconds` 时的默认轮询间隔。 |
| `AGENT_SERVICE_HTTP_TIMEOUT_SECONDS` | `75` | 调用 Multica HTTP API 的超时时间。 |
| `AGENT_SERVICE_RESULT_RECENT_COMMENTS` | `3` | 节点完成后读取最近多少条评论作为结果候选。 |
| `AGENT_SERVICE_RESULT_TEXT_LIMIT` | `1200` | 写入节点结果中的评论文本截断长度。 |
| `AGENT_SERVICE_MESSAGE_TEXT_LIMIT` | `1000` | trace message 文本截断长度。 |
| `AGENT_SERVICE_FETCH_RUN_MESSAGES` | `false` | 是否在节点完成时把最新 run messages 也拉入节点结果。 |
| `SANDBOX_API_BASE` | `http://127.0.0.1:8765` | Sandbox 自身 base URL，用于 callback metadata 和兼容路径。 |

## 3. 任务图中如何选择 Multica

节点配置中设置：

```json
{
  "id": "architecture_review",
  "operation": "agent_task",
  "deps": ["prepare_brief"],
  "agent": {
    "backend": "multica",
    "agent_key": "architect",
    "context_policy": "provided_context_only",
    "runtime_profile": "codex",
    "execution_mode": "multica",
    "priority": "high",
    "status": "todo",
    "poll_seconds": 30,
    "prompt": "请基于上游结果完成架构评审，并把最终结论写入评论。"
  },
  "timeout_seconds": 7200
}
```

关键字段：

| 字段 | 说明 |
|---|---|
| `agent.backend` | 设置为 `multica` 后走 Multica Agent Service。 |
| `agent.agent_key` | 指定 Multica 中要调度的 Agent key。 |
| `agent.agent_id` | 可选，直接指定 Agent id。 |
| `agent.agent_name` | 可选，指定展示或请求的 Agent 名称。 |
| `agent.context_policy` | 当前常用 `provided_context_only`，表示只发送图输入、节点 prompt 和上游结果。 |
| `agent.runtime_profile` | 传给 Multica 的 runtime profile，例如 `codex`、`full_multica`、`lightweight`。 |
| `agent.execution_mode` | 传给 Multica 的执行模式，例如 `multica`、`codex_cli`、`lightweight_hermes_oneshot`。 |
| `agent.prompt` | 节点给 Agent 的任务指令。 |
| `timeout_seconds` | Temporal workflow 等待该节点完成的上限。 |

## 4. 当前调用链

```text
Client / Web
  -> Sandbox POST /api/tasks
  -> Temporal JsonDagWorkflow
  -> A2A runtime provider
  -> Multica POST /tasks
  -> Temporal workflow durable polling
  -> Multica GET /tasks/{task_id}
  -> Multica GET /tasks/{task_id}/comments
  -> Multica GET /tasks/{task_id}/runs
  -> optional GET /runs/{run_id}/messages
  -> A2A artifact
  -> Temporal workflow signal / resume internally
  -> downstream nodes receive dependency_results
```

当前完成唤醒不是由 Multica 主动回调完成，而是 Temporal workflow 周期性执行 polling activity。每次 poll 结束后 workflow 可以继续持久挂起，不持续占用 worker 线程。

## 5. Sandbox 调用 Multica 的 API

### 5.1 创建 Multica Task

```http
POST /tasks
Content-Type: application/json
```

当前请求体由持久化执行层生成，主要字段如下：

```json
{
  "title": "MAOS Control-Flow Node Task: architecture_review",
  "description": "节点任务说明、图输入、上游结果和执行约束",
  "agent_key": "architect",
  "agent_id": null,
  "agent_name": "架构师 Agent",
  "priority": "medium",
  "status": "todo",
  "project_id": null,
  "parent_id": null,
  "allow_duplicate": true,
  "metadata": {
    "maos_task": true,
    "maos_backend": "multica",
    "workflow_id": "workflow-id",
    "node_id": "architecture_review",
    "a2a_task_id": "a2a-task-...",
    "idempotency_key": "...",
    "context_id": "...",
    "operation": "agent_task",
    "execution_mode": "multica",
    "runtime_profile": "codex",
    "context_policy": "provided_context_only",
    "comment_history_policy": "disabled",
    "metadata_policy": "disabled",
    "skill_loading_policy": "none",
    "tool_policy": "no_external_tools",
    "requested_agent_key": "architect",
    "dispatch_agent_key": "architect",
    "dependency_node_ids": "prepare_brief",
    "dependency_result_bytes": 1234,
    "graph_input_bytes": 567
  }
}
```

说明：

- `description` 是真实 Agent 看到的主要任务输入，包含节点 prompt、A2A context、上游结果摘要和执行规则。
- `metadata` 用于标识这是 MAOS/Sandbox 派发的任务，并让 Multica runtime 进入更受控的启动模式。
- 默认不把完整 `dependency_results_json` 和 `graph_input_json` 放进 metadata，除非节点显式开启 `include_payload_metadata`。这样做是为了减少 Multica 侧 token 膨胀。
- 创建前会先通过 `GET /tasks?limit=200` 查找相同 `idempotency_key` 的历史任务，避免重复创建。

### 5.2 按 idempotency key 查重

```http
GET /tasks?limit=200
```

持久化执行层会遍历返回任务，检查：

```json
{
  "metadata": {
    "idempotency_key": "..."
  }
}
```

若找到相同 idempotency key，则复用已有 Multica task，而不是再次 `POST /tasks`。

### 5.3 查询任务状态

```http
GET /tasks/{multica_task_id}
```

当前主要读取字段：

```json
{
  "id": "...",
  "title": "...",
  "status": "todo | in_progress | in_review | done | blocked | cancelled",
  "metadata": {}
}
```

状态映射：

| Multica status | Sandbox / A2A 处理 |
|---|---|
| `done` | 视为完成。 |
| `in_review` | 视为完成。 |
| `blocked` | 视为失败。 |
| `cancelled` | 视为失败。 |
| `canceled` | 视为失败。 |
| 其他状态 | 视为仍在执行，继续轮询。 |

### 5.4 读取最终输出评论

```http
GET /tasks/{multica_task_id}/comments?recent={n}
```

默认 `n = AGENT_SERVICE_RESULT_RECENT_COMMENTS`，当前为 `3`。

持久化执行层从最近评论中提取最终输出：

- 优先取最新评论文本。
- 文本会按 `AGENT_SERVICE_RESULT_TEXT_LIMIT` 截断后写入节点结果的 `latest_comment`。
- 如果评论文本中能解析出 JSON 对象，则写入 `structured_output`，并把其中的顶层字段提升到节点 payload 中。

节点完成后的结果 payload 示例：

```json
{
  "status": "done",
  "agent_backend": "multica",
  "agent_key": "architect",
  "requested_agent_key": "architect",
  "context_policy": "provided_context_only",
  "runtime_profile": "codex",
  "execution_mode": "multica",
  "agent_id": null,
  "agent_name": "架构师 Agent",
  "agent_service_task_id": "multica-task-id",
  "title": "MAOS Control-Flow Node Task: architecture_review",
  "latest_comment": "Agent 最终输出文本",
  "comments": [],
  "runs": [],
  "messages": [],
  "structured_output": {
    "decision": "approved",
    "reason": "..."
  }
}
```

### 5.5 读取 run 列表

```http
GET /tasks/{multica_task_id}/runs
```

用途：

- Web 看板展示 Agent 执行链路。
- 完成节点时保存 compact run summary。
- 找出最新 run，用于可选读取 messages。

### 5.6 读取 run messages

```http
GET /runs/{run_id}/messages?issue_id={multica_task_id}
```

用途：

- Web `Agent Trace` 面板展示更详细的执行链路。
- 如果 `AGENT_SERVICE_FETCH_RUN_MESSAGES=true`，节点完成时也会把 compact messages 写入节点 payload。

默认不在节点结果中拉取 run messages，避免大结果进入 Temporal history。

## 6. Sandbox 暴露给 Multica / Agent 的 API

### 6.1 当前兼容回调入口

```http
POST /api/agent-callbacks
POST /api/v1/agent-events
```

当前此入口存在，并会把 callback 转换为 workflow signal：

```json
{
  "workflow_id": "workflow-id",
  "node_id": "node-id",
  "a2a_task_id": "a2a-task-id",
  "status": "completed",
  "result": {}
}
```

处理流程：

1. `web_visualize.py` 接收请求。
2. `TemporalTaskService.handle_agent_callback()` 调用 `complete_task_from_agent_callback()`。
3. 生成 A2A task event。
4. 调用 Temporal workflow signal：`JsonDagWorkflow.agent_node_completed`。

重要说明：

- 当前 Multica 主路径没有使用该 push callback。
- 当前 Multica 主路径是 durable polling：Sandbox 主动查 `GET /tasks/{id}`。
- 这个回调入口主要保留给 simulator、未来 Agent push event 或 A2A push 兼容。

## 7. Web 看板直接读取 Multica 的 API

Web 服务为了展示真实 Agent 节点的输入、最终输出和 trace，会直接代理读取 Multica：

### 7.1 Agent Trace

```http
GET /api/agent-trace?task_id={multica_task_id}
```

Sandbox 内部会调用：

```http
GET /tasks/{task_id}
GET /tasks/{task_id}/runs
GET /tasks/{task_id}/comments?recent=20
GET /runs/{latest_run_id}/messages?issue_id={task_id}
```

返回给前端的主要字段：

```json
{
  "ok": true,
  "agent_service": "http://127.0.0.1:8091",
  "task": {},
  "agent_input": {},
  "run": {},
  "final_output": {},
  "summary": {},
  "timeline": [],
  "steps": []
}
```

### 7.2 Agent Input

```http
GET /api/agent-input?task_id={multica_task_id}
```

Sandbox 内部调用：

```http
GET /tasks/{task_id}
```

并从 Multica task 的 `description`、`metadata` 等字段中提取启动 Agent 时注入的任务输入。

## 8. A2A 数据传递方式

Multica 本身接收的是 Multica Task；持久化执行层内部将其包装成 A2A task/artifact，以便下游节点统一读取依赖结果。

完成后生成的 artifact：

```json
{
  "name": "dag-node-result",
  "parts": [
    {
      "mediaType": "application/json",
      "data": {
        "node": "architecture_review",
        "operation": "agent_task",
        "duration_seconds": 123.4,
        "payload": {
          "latest_comment": "...",
          "structured_output": {}
        }
      }
    }
  ]
}
```

下游节点收到的依赖上下文形态：

```json
{
  "dependency_results": {
    "architecture_review": {
      "node": "architecture_review",
      "operation": "agent_task",
      "duration_seconds": 123.4,
      "payload": {}
    }
  }
}
```

也就是说，上游 Multica Agent 的最终输出不是直接作为纯文本拼接给下游，而是先被固化为 A2A artifact，再由下游 provider 将 artifact 转换为 `dependency_results` 并注入新的任务描述。

## 9. 完成与失败语义

### 完成

当 `GET /tasks/{id}` 返回：

- `status = done`
- 或 `status = in_review`

持久化执行层会：

1. 读取 comments、runs、可选 messages。
2. 构造节点 result。
3. 创建 `dag-node-result` artifact。
4. 生成 completed event。
5. 唤醒 Temporal workflow。
6. 继续调度下游节点。

### 失败

当 `GET /tasks/{id}` 返回：

- `status = blocked`
- `status = cancelled`
- `status = canceled`

持久化执行层会：

1. 构造 failed event。
2. 标记节点失败。
3. 唤醒 workflow。
4. workflow 按图执行策略进入失败或后续错误处理。

## 10. 幂等与重复创建控制

当前 Multica 对接已经做了两层幂等：

1. A2A task id 由节点 id 和 idempotency key 生成。
2. 创建 Multica task 前调用 `GET /tasks?limit=200`，查找相同 `metadata.idempotency_key`。

如果找到已有任务，则复用已有 Multica task，不再次创建。

这个机制用于避免 Temporal activity retry 或服务重启恢复时反复创建新的 “MAOS Control-Flow Node Task”。

## 11. 当前限制

- Multica 主路径仍是 polling，不是 Multica 主动 push event。
- `GET /tasks?limit=200` 的查重是简化实现，任务很多时需要服务端索引或专门查询参数。
- 节点结果中的 `latest_comment` 会被截断；完整原始输出应从 Multica comments 或未来 artifact store 获取。
- 默认不抓取 run messages 到 workflow result，避免 Temporal history 过大。
- Web trace 是展示用途，不是 workflow 调度所依赖的权威数据。
- 当前没有调用 Multica cancel API；任务取消、节点取消还需要补齐。
- 当前没有将大型 artifact 外置到对象存储，仍以 compact JSON 写入 workflow result。

## 12. 代码位置

| 文件 | 作用 |
|---|---|
| `a2a_runtime.py` | Multica provider、创建任务、轮询状态、结果转换、A2A artifact。 |
| `dag_workflow.py` | Temporal workflow 调度、持久等待、节点完成 signal、最终 workflow result。 |
| `sandbox_runtime.py` | Sandbox 与 Temporal client 的桥接，处理 callback 并 signal workflow。 |
| `web_visualize.py` | Web API、看板、Agent input/output/trace 展示，代理查询 Multica。 |


# 持久化执行层与 Multica 的当前接口 API

> 当前完整 API 参考见 [`api_reference.md`](api_reference.md)。本文档保留为 Multica 集成说明；稳定运行路径已切到 Agent Service API v1（`/api/v1/agent-tasks`），旧 `/tasks` 接口只作为 Multica 兼容与排障入口。

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
  -> Agent Service POST /api/v1/agent-tasks
  -> Temporal workflow durable polling
  -> Agent Service GET /api/v1/agent-tasks/{task_id}
  -> optional legacy Multica GET /tasks/{task_id}/comments
  -> optional legacy Multica GET /tasks/{task_id}/runs
  -> optional legacy GET /runs/{run_id}/messages
  -> A2A artifact
  -> Temporal workflow signal / resume internally
  -> downstream nodes receive dependency_results
```

当前完成唤醒不是由 Multica 主动回调完成，而是 Temporal workflow 周期性执行 polling activity。每次 poll 结束后 workflow 可以继续持久挂起，不持续占用 worker 线程。

## 5. Sandbox 调用 Agent Service v1 的 API

当前主路径是 Sandbox / Temporal provider 调用 Agent Service v1。Agent
Service facade 内部可以继续适配 Multica 的 legacy `/tasks`、`comments`、
`runs` 和 `messages` 数据模型，但 workflow/provider 不再直接依赖这些旧
endpoint。

### 5.1 创建或幂等复用 Agent Task

```http
POST /api/v1/agent-tasks
Content-Type: application/json
```

请求体由持久化执行层生成，稳定字段如下：

```json
{
  "idempotency_key": "workflow:node#1:dispatch",
  "agent": {
    "backend": "multica",
    "agent_key": "architect",
    "agent_id": null,
    "agent_name": "架构师 Agent"
  },
  "input": {
    "title": "MAOS Control-Flow Node Task: architecture_review",
    "instruction": "节点 prompt、图输入、上游结果摘要和执行约束",
    "context": {
      "graph_input": {},
      "dependencies": {}
    }
  },
  "runtime": {
    "mode": "async",
    "timeout_seconds": 86400,
    "context_policy": "provided_context_only",
    "runtime_profile": "codex",
    "execution_mode": "multica",
    "priority": "medium",
    "status": "in_progress",
    "allow_duplicate": true
  },
  "callback": {
    "url": null,
    "payload": {
      "workflow_id": "workflow-id",
      "node_id": "architecture_review",
      "a2a_task_id": "a2a-task-..."
    }
  },
  "metadata": {
    "maos_task": true,
    "maos_backend": "multica",
    "workflow_id": "workflow-id",
    "node_id": "architecture_review",
    "operation": "agent_task"
  }
}
```

说明：

- `input.instruction` 是真实 Agent 看到的主要任务输入。
- `idempotency_key` 用于防止 Temporal activity retry 或服务恢复时重复创建
  Agent task。
- Agent Service 当前的 Multica facade 可能通过 legacy
  `GET /tasks?limit=200` 扫描 `metadata.idempotency_key` 做兼容查重；这是实现
  细节，不是持久化执行层对外的稳定 API。
- 默认不把完整 `dependency_results_json` 和 `graph_input_json` 放进 metadata，
  除非节点显式开启 `include_payload_metadata`。

### 5.2 查询任务状态

```http
GET /api/v1/agent-tasks/{agent_task_id}
```

返回稳定 projection：

```json
{
  "api_version": "agent-service-v1",
  "task_id": "agent-task-id",
  "external_id": "multica-task-id",
  "backend": "multica",
  "status": "working",
  "state": "TASK_STATE_WORKING",
  "mode": "poll",
  "poll_after_seconds": 30,
  "progress": {
    "phase": "working",
    "message": "当前任务摘要",
    "percent": null
  },
  "input_request": null,
  "metadata": {},
  "links": {
    "self": "/api/v1/agent-tasks/agent-task-id",
    "events": "/api/v1/agent-tasks/agent-task-id/events",
    "artifacts": "/api/v1/agent-tasks/agent-task-id/artifacts"
  }
}
```

状态映射：

| Agent Service status | A2A state | Sandbox 处理 |
|---|---|---|
| `accepted` | `TASK_STATE_WORKING` | 继续持久等待。 |
| `working` | `TASK_STATE_WORKING` | 继续持久等待。 |
| `input_required` | `TASK_STATE_INPUT_REQUIRED` | 创建 human intervention 并等待人工输入。 |
| `completed` | `TASK_STATE_COMPLETED` | 提取结果并调度下游节点。 |
| `failed` | `TASK_STATE_FAILED` | 标记节点失败。 |
| `timed_out` | `TASK_STATE_FAILED` | 标记节点失败。 |
| `cancelled` | `TASK_STATE_CANCELED` | 标记节点取消。 |

### 5.3 读取事件和 Trace

```http
GET /api/v1/agent-tasks/{agent_task_id}/events?since=0&poll_seconds=30
```

该接口返回 Agent task 的事件、心跳或 trace 流。Multica facade 内部可以把
legacy runs/messages 转换为统一事件。Web UI 通常通过 Sandbox 的
`GET /api/agent-trace?task_id={agent_task_id}` 查看整理后的中文步骤和耗时。

### 5.4 读取 Artifact

```http
GET /api/v1/agent-tasks/{agent_task_id}/artifacts
GET /api/v1/agent-tasks/{agent_task_id}/artifacts/{artifact_id}
GET /api/v1/agent-tasks/{agent_task_id}/artifacts/{artifact_id}/content
```

完成节点时，provider 会把 Agent Service projection、最终输出、trace 摘要和
artifact 描述转换为 A2A `dag-node-result`，再写入 Sandbox 的 artifact store。

### 5.5 人工输入与取消

```http
POST /api/v1/agent-tasks/{agent_task_id}/resume
POST /api/v1/agent-tasks/{agent_task_id}/cancel
```

当 Agent Service 返回 `input_required` 时，Sandbox 会创建统一的人在回路
intervention。人工响应提交到 Sandbox 后，workflow 再通过 `resume` 把响应传回
原 Agent task。取消 workflow 或节点时，provider 可以通过 `cancel` 尝试取消外部
Agent task。

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

1. Sandbox API service 接收请求。
2. `TemporalTaskService.handle_agent_callback()` 调用 `complete_task_from_agent_callback()`。
3. 生成 A2A task event。
4. 调用 Temporal workflow signal：`JsonDagWorkflow.agent_node_completed`。

重要说明：

- 当前 Multica 主路径没有依赖该 push callback。
- 当前 Multica 主路径是 durable polling：Sandbox 主动查 `GET /api/v1/agent-tasks/{id}`。
- 这个回调入口主要保留给 simulator、未来 Agent push event 或 A2A push 兼容。

## 7. Web 看板读取 Agent 调试信息的 API

Web 服务为了展示真实 Agent 节点的输入、最终输出和 trace，会调用 Sandbox API。
Sandbox 再根据 provider task 信息读取 Agent Service v1、provider task store 或
legacy Multica 调试数据。

### 7.1 Agent Trace

```http
GET /api/agent-trace?task_id={multica_task_id}
```

Multica provider 的调试实现可以读取：

```http
GET /api/v1/agent-tasks/{task_id}
GET /api/v1/agent-tasks/{task_id}/events
GET /api/v1/agent-tasks/{task_id}/artifacts
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

Sandbox 内部读取 Agent Service v1 projection 或 provider task store：

```http
GET /api/v1/agent-tasks/{task_id}
```

并从 Agent Service projection、provider task store 或兼容 Multica task 的
`description`、`metadata` 等字段中提取启动 Agent 时注入的任务输入。

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

当 `GET /api/v1/agent-tasks/{id}` 返回：

- `status = completed`

持久化执行层会：

1. 读取 projection、events、artifacts 或 provider task store 中的输出。
2. 构造节点 result。
3. 创建 `dag-node-result` artifact。
4. 生成 completed event。
5. 唤醒 Temporal workflow。
6. 继续调度下游节点。

### 失败

当 `GET /api/v1/agent-tasks/{id}` 返回：

- `status = failed`
- `status = timed_out`
- `status = cancelled`

持久化执行层会：

1. 构造 failed event。
2. 标记节点失败。
3. 唤醒 workflow。
4. workflow 按图执行策略进入失败或后续错误处理。

## 10. 幂等与重复创建控制

当前 Multica 对接已经做了两层幂等：

1. A2A task id 由节点 id 和 idempotency key 生成。
2. Agent Service v1 使用 `idempotency_key` 复用已有 Agent task；Multica facade 内部可以用 legacy `/tasks` 查询实现兼容查重。

如果找到已有任务，则复用已有 Agent task，不再次创建。

这个机制用于避免 Temporal activity retry 或服务重启恢复时反复创建新的 “MAOS Control-Flow Node Task”。

## 11. 当前限制

- Multica 主路径仍是 polling，不是 Multica 主动 push event。
- Multica facade 的 legacy `/tasks` 查重是兼容实现，任务很多时需要服务端索引或专门查询参数。
- Web trace 是展示用途，不是 workflow 调度所依赖的权威数据。
- cancel/resume 的实际效果取决于后端 Agent Service 是否支持真正取消或原生恢复。
- 大型业务输出优先通过 Sandbox artifact store 外置；trace、runs、comments 等运行过程数据仍应避免完整写入 Temporal history。

## 12. 代码位置

| 文件 | 作用 |
|---|---|
| `maos_runtime.a2a` / `maos_runtime.a2a.providers` | Provider facade、Multica provider、创建任务、轮询状态、结果转换、A2A artifact。 |
| `maos_runtime.workflows.json_dag` / `maos_runtime.workflows.activities` | Temporal workflow 调度、持久等待、节点完成 signal、最终 workflow result。 |
| `maos_runtime.sandbox.service` / `services.sandbox_api_service` | Sandbox 与 Temporal client 的桥接，HTTP API、callback、human response 和 workflow signal。 |
| `web.web_agent_api` / `web.web_ui_server` | Web 看板的 Agent input/output/trace 展示与 Sandbox API 代理。 |

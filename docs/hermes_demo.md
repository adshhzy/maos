# Hermes Backend 使用说明

`backend: "hermes"` 表示任务图节点通过 MAOS Agent Service API v1 调用 Hermes lightweight/one-shot 运行模式。它复用统一的 Provider Runtime 生命周期和 A2A 数据结构，但不经过 Multica 的任务评论/status 工作流。

## 运行路径

```text
Temporal workflow
  -> Provider Runtime API v1
  -> HermesOneshotProvider
  -> Agent Service API v1 POST /api/v1/agent-tasks
  -> Hermes CLI lightweight/one-shot runtime
  -> Agent Service API v1 GET /api/v1/agent-tasks/{task_id}
  -> A2A Task / Artifact
  -> workflow resume
```

Temporal workflow 在 Hermes 执行期间使用 durable timer + 短轮询 activity，不占用 worker 线程长时间等待。

## 节点配置

```json
{
  "id": "hermes_fast_review",
  "label": "Hermes 快速评审",
  "operation": "agent_task",
  "deps": ["draft"],
  "agent": {
    "backend": "hermes",
    "agent_key": "fast_reviewer",
    "context_policy": "provided_context_only",
    "runtime_profile": "hermes_oneshot",
    "execution_mode": "hermes_oneshot",
    "poll_seconds": 30,
    "timeout_seconds": 900,
    "artifact_transfer_mode": "inline",
    "prompt": "请只基于原始任务和上游 draft 结果完成快速评审，并输出结论。"
  }
}
```

兼容别名包括 `hermes-oneshot`、`direct-hermes`、`hermes-direct`，都会被 provider registry 归一化为 `hermes`。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `AGENT_SERVICE_API_BASE` | Agent Service API 地址，默认 `http://127.0.0.1:8091`。 |
| `HERMES_BIN` | Hermes 可执行文件路径。 |
| `HERMES_WORKDIR` | Hermes 运行工作目录。 |
| `HERMES_PROVIDER_MODEL` | 默认模型。 |
| `HERMES_PROVIDER_PROVIDER` | 默认模型供应商。 |
| `HERMES_PROVIDER_POLL_SECONDS` | provider 默认轮询间隔。 |
| `HERMES_PROMPT_PAYLOAD_LIMIT` | prompt 载荷上限。 |
| `HERMES_GIT_BASH_PATH` | Windows 上需要 Git Bash 时的路径。 |

## 和 Multica Backend 的区别

| 项目 | `backend: "hermes"` | `backend: "multica"` |
| --- | --- | --- |
| 外部 API | Agent Service API v1 | Agent Service API v1 |
| 底层 runtime | Hermes lightweight/one-shot | Multica daemon 中的目标 Agent |
| 是否创建 Multica task | 否 | 是 |
| 是否依赖 Multica comment/status | 否 | 是 |
| 适用场景 | 快速分析、轻量评审、低开销节点 | 需要 Multica 角色 Agent、技能和界面协作的节点 |

如果需要走 Multica 但内部由 Hermes 执行，可使用 `backend: "multica"` 并在 Multica/Agent Service 配置中选择对应 execution mode。任务图里直接写 `backend: "hermes"` 时，语义是“绕开 Multica task/comment/status 编排，直接通过 Agent Service facade 调 Hermes”。

## 相关示例

```text
examples/all_hermes_chinese_knowledge_assistant_launch.json
examples/mixed_hermes_multica_simulator.json
examples/all_hermes_control_flow_branch_loop_knowledge_release.json
```

## 排查

- Sandbox API 健康检查：`GET http://127.0.0.1:8766/api/health`
- Agent Service 健康检查：`GET http://127.0.0.1:8091/health`
- Agent Service v1 capabilities：`GET http://127.0.0.1:8091/api/v1/capabilities`
- Web UI 节点详情里可查看 Agent Input、Agent Trace、Agent Final Output 和 artifacts。

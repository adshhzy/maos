# MAOS 任务图 JSON 格式说明

本文档描述当前持久化多 Agent 编排内核支持的任务图 JSON 格式。其他 Agent 或外部客户端可以根据本文档生成可被 Web UI、Sandbox API 和 Temporal workflow 执行的任务图文件。

正式 JSON Schema 位于：

```text
schemas/task_graph.schema.json
```

命令行校验：

```powershell
.\.venv\Scripts\python.exe -m maos_runtime.graph.validate examples
.\.venv\Scripts\python.exe -m maos_runtime.graph.validate example_cmp\multi_agent_coding_async_ttl_cache_hard_concurrency_compact_review_claude.json
```

## 1. 基本结构

一个任务图是一个 JSON object，最少包含 `id` 和 `nodes`：

```json
{
  "id": "unique-graph-id",
  "name": "中文展示名称",
  "graph_type": "control_flow",
  "input": {
    "goal": "用户原始任务"
  },
  "start": "intake",
  "max_total_visits": 20,
  "execution_policy": {
    "mode": "parallel"
  },
  "nodes": [],
  "edges": []
}
```

顶层字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定图 ID。建议只使用字母、数字、`_`、`-`、`.`。 |
| `name` | string | 否 | Web UI 展示名称，可使用中文。 |
| `graph_type` | string | 否 | `dag` 或 `control_flow`。有显式 `edges` 时推荐写 `control_flow`。 |
| `type` | string | 否 | `graph_type` 的兼容别名。 |
| `input` | object | 否 | 图级输入。节点 `params` 可用 `$input.xxx` 引用，Agent prompt 会收到该输入。 |
| `start` | string 或 string[] | 否 | 起始节点。未填写时从无入边节点推断；有循环时建议显式填写。 |
| `start_nodes` | string[] | 否 | 多起点写法，与 `start` 二选一即可。 |
| `default_join` | string | 否 | 默认入边汇合策略：`all`、`any`、`race`、`first`。显式 `edges` 图默认 `any`，DAG 简写默认 `all`。 |
| `max_total_visits` | number | 否 | 整张图最多节点执行次数，用于防止无限循环。 |
| `execution_policy` | object | 否 | 图级调度策略，详见第 5 节。 |
| `executionPolicy` | object | 否 | `execution_policy` 的兼容别名。 |
| `nodes` | array | 是 | 节点列表，每个节点必须有唯一 `id`。 |
| `edges` | array | 否 | 显式控制流边。省略时会根据每个节点的 `deps` 生成传统 DAG。 |

## 2. 节点格式

通用节点结构：

```json
{
  "id": "node_id",
  "label": "节点展示名",
  "type": "agent",
  "operation": "agent_task",
  "deps": ["upstream_node_id"],
  "join": "all",
  "max_visits": 1,
  "params": {},
  "agent": {
    "backend": "claude",
    "prompt": "完成该节点的业务子任务。"
  },
  "timeout_seconds": 7200
}
```

节点字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 节点 ID，必须唯一。 |
| `label` | string | 否 | Web UI 上的展示名称。 |
| `type` | string | 否 | `agent`、`condition`、`decision`、`router`、`branch`、`human`、`human_node`、`human_intervention`、`approval`。默认是 `agent`。 |
| `operation` | string | 否 | 执行动作。真实 Agent 节点通常用 `agent_task`；模拟节点可用 `emit`、`merge`、`join`、`status`、`template` 等。 |
| `deps` | string[] | 否 | 该节点需要读取的上游节点结果。即使使用显式 `edges`，也建议为需要读取结果的节点填写 `deps`。 |
| `join` | string | 否 | 入边触发策略：`all` 等待所有前置；`any` 任一前置到达即可触发，常用于分支汇合和循环回退节点。 |
| `max_visits` | number | 否 | 节点最多可执行次数。循环中的节点必须设置合理上限。 |
| `max_attempts` | number | 否 | `max_visits` 的兼容别名。 |
| `timeout_seconds` | number | 否 | 节点最长等待时间。真实 Agent 节点可设置为数小时。 |
| `poll_seconds` | number | 否 | 节点轮询间隔。也可放在 `agent.poll_seconds`。 |
| `result_text_limit` | number | 否 | 节点结果保留长度上限。需要下游完整消费时可调大。 |
| `params` | object | 否 | Simulator/control 节点参数。 |
| `simulate` | object | 否 | Simulator 耗时和人工介入模拟配置。 |
| `agent` | object | 否 | Agent provider 配置。 |
| `prompt` | string | 否 | 节点级指令。若 `agent.prompt` 缺省，会使用该字段。 |
| `schema` / `response_schema` | object | 否 | 人工节点或评审门期望输出结构说明。 |

### Agent 配置

`agent` 字段用于声明节点由哪个 provider/backend 执行：

```json
{
  "agent": {
    "backend": "claude",
    "agent_key": "reviewer",
    "context_policy": "provided_context_only",
    "runtime_profile": "maos_compact_agent",
    "execution_mode": "normal",
    "poll_seconds": 30,
    "timeout_seconds": 7200,
    "result_text_limit": 120000,
    "artifact_transfer_mode": "inline",
    "prompt": "请基于原始任务和上游结果完成该节点。"
  }
}
```

常用字段：

| 字段 | 说明 |
| --- | --- |
| `backend` | 后端类型。当前内置 `simulator`、`multica`、`hermes`、`codex`、`claude`、`claude-huawei`、`evaluator`。 |
| `agent_key` | 外部 Agent 选择键。Multica 常用；本地 CLI provider 可忽略或作为展示/路由提示。 |
| `agent_id` / `agent_name` | 外部 Agent 精确选择字段，主要用于 Multica。 |
| `context_policy` | 上下文策略。常用 `provided_context_only`，表示只基于任务输入和上游结果。 |
| `runtime_profile` / `execution_mode` | provider 运行模式。Multica/Hermes 可用来选择 compact/lightweight 运行方式。 |
| `poll_seconds` | Temporal durable polling 间隔。 |
| `timeout_seconds` | provider 任务超时。 |
| `result_text_limit` | provider 输出投影保留长度。 |
| `prompt_payload_limit` / `description_payload_limit` | prompt 或描述载荷上限。 |
| `truncate_prompt_payload` | 是否允许截断 prompt 载荷。 |
| `artifact_transfer_mode` | 上游 artifact 传递方式：`ref` 或 `inline`。 |
| `dependency_artifact_mode` | `artifact_transfer_mode` 的兼容别名。 |
| `prompt` | 传给 Agent 的节点业务指令。 |

### 内置 backend

| Backend | Provider | 适用场景 |
| --- | --- | --- |
| `simulator` | `SimulatorProvider` | 本地模拟执行、单元测试、人工介入流程模拟。 |
| `multica` | `MulticaProvider` | 通过 Agent Service API v1 调用 Multica daemon 中的真实 Agent。 |
| `hermes` | `HermesOneshotProvider` | 通过 Agent Service API v1 调用 Hermes lightweight/one-shot 运行模式，不走 Multica 任务评论流。 |
| `codex` | `CodexCliProvider` | 本地 Codex CLI one-shot Agent runtime。 |
| `claude` | `ClaudeCliProvider` | 本地 Claude CLI Agent runtime。 |
| `claude-huawei` | `ClaudeHuaweiCliProvider` | 本地 Claude CLI 路由到 Huawei/DeepSeek 的运行配置。 |
| `evaluator` | `EvaluatorProvider` | 确定性本地评测，不调用 LLM。 |

兼容别名由 provider registry 归一化，例如 `direct-hermes` -> `hermes`、`codex-cli` -> `codex`、`deterministic-evaluator` -> `evaluator`。

## 3. 边、分支和循环

显式控制流图使用 `edges` 描述节点之间的流转：

```json
{
  "from": "review_gate",
  "to": "final_package",
  "when": "last.decision == 'approved'",
  "label": "评审通过",
  "kind": "branch"
}
```

边字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `from` | string | 是 | 起点节点 ID。 |
| `to` | string | 是 | 目标节点 ID。 |
| `when` | string/bool/null | 否 | 条件表达式。空值表示无条件流转。 |
| `label` | string | 否 | Web UI 展示标签。 |
| `kind` | string | 否 | `control`、`dependency`、`branch`、`loop`、`fallback`。UI 会用它区分边样式。 |

条件表达式在 Temporal workflow 内部根据已记录状态求值，不能有副作用。可读取：

| 名称 | 含义 |
| --- | --- |
| `last` / `result` | 当前 `from` 节点本次运行输出 payload。 |
| `results` / `deps` | 所有已完成节点的最新结果和实例结果。 |
| `input` | 图级输入。 |
| `visits` / `attempts` | 节点访问计数。 |
| `node.id` / `node.visit` | 当前出边源节点信息。 |

支持比较、布尔逻辑、成员判断、属性访问、下标访问，以及 `len`、`int`、`float`、`str`、`bool`、`min`、`max`。

常见条件：

```text
last.decision == 'approved'
last.decision == 'needs_revision'
last.decision in ['approved', 'risk_accepted']
visits.review_gate < 3
len(last.required_changes) > 0
```

如果某个节点所有出边都带 `when`，但没有任何条件匹配，workflow 会失败。因此真实 Agent 评审门必须输出稳定字段，例如：

```json
{
  "decision": "approved",
  "reason": "通过原因",
  "required_changes": [],
  "confidence": 0.92
}
```

循环就是指向上游节点的边。循环图必须设置 `max_visits` 和/或 `max_total_visits`：

```json
{
  "from": "review_gate",
  "to": "implementation",
  "when": "last.decision == 'needs_revision' and visits.implementation < 3",
  "label": "按审查意见修订",
  "kind": "loop"
}
```

当控制流再次到达一个已经达到 `max_visits` 的节点时，workflow 会失败并提示访问次数上限，避免无限循环。

## 4. 上游结果和 Artifact 传递

每个 Agent 节点会收到：

- 图级 `input`。
- 当前节点的业务 `prompt`。
- `deps` 声明的上游节点结果。
- 触发当前节点的入边源节点结果。
- A2A `Message`、`Task`、`Artifact` 形态的运行元数据。

依赖结果可用两种模式传给下游：

| 模式 | 配置 | 说明 |
| --- | --- | --- |
| `ref` | `artifact_transfer_mode: "ref"` | A2A 中只传 `artifact_ref`、`uri`、`content_hash`、`mime_type`、`size`、`summary`。下游 Agent 需要能通过 artifact API 拉取完整内容。 |
| `inline` | `artifact_transfer_mode: "inline"` | 直接把上游业务内容放入下游输入。会移除 trace、comments、runs、messages 等运行噪声字段，但不会截断业务长文本。适合不能访问 artifact API 的 CLI/第三方 Agent。 |

全局默认可用环境变量控制：

```text
A2A_DEPENDENCY_ARTIFACT_MODE=ref
A2A_DEPENDENCY_ARTIFACT_MODE=inline
```

节点级配置优先级高于环境变量：

```json
{
  "agent": {
    "backend": "claude",
    "artifact_transfer_mode": "inline"
  }
}
```

Simulator `params` 支持路径引用：

| 引用 | 含义 |
| --- | --- |
| `$input.goal` | 图级输入中的 `goal` 字段。 |
| `$deps.node_id.status` | 上游节点最新 payload 的 `status` 字段。 |
| `$deps.review_gate.decision` | 评审门的结构化决策。 |
| `$deps.implementation.latest_comment` | Agent 节点最终输出摘要/正文。 |

循环修订节点如果需要看到前一轮自己的结果，应把自己的节点 ID 或评审门节点 ID 纳入 `deps`，并在 prompt 中明确“基于上一轮输出和本轮评审意见定向修订，不要从零重写”。

## 5. 调度策略

默认情况下，所有 ready 节点会并行调度。对容易触发速率限制的 backend，可在图级设置串行或限流模式：

```json
{
  "execution_policy": {
    "mode": "serial",
    "max_concurrent_nodes": 1
  }
}
```

字段：

| 字段 | 说明 |
| --- | --- |
| `mode` | `parallel` 或 `serial`。兼容 `sequential`、`single`、`one_at_a_time`。 |
| `node_scheduling` | `mode` 的兼容别名。 |
| `max_concurrent_nodes` | 同时运行的节点上限。 |
| `maxConcurrentNodes` | `max_concurrent_nodes` 的兼容别名。 |

这只影响同一张图内 ready 节点的调度，不会改变其他任务图的并发模式。

## 6. Simulator 节点

Simulator 节点用于本地测试、轻量数据组装、耗时模拟和人工介入模拟。常用 operation：

| Operation | 说明 |
| --- | --- |
| `emit` | 解析并返回 `params`。常用于起始上下文整理。 |
| `merge` | 返回 `params` 和依赖 payload。 |
| `template` | 返回 `params.fields` 解析后的对象。 |
| `count` | 对 `params.values` 解析后计数。 |
| `percentage_from_count` | 根据 count 计算比例。 |
| `status` | 返回状态和 details。 |
| `join` | 显式组装 `params.fields` 中声明的字段。 |

示例：

```json
{
  "id": "intake",
  "label": "整理输入",
  "operation": "emit",
  "agent": {
    "backend": "simulator"
  },
  "params": {
    "goal": "$input.goal"
  },
  "simulate": {
    "min_seconds": 2,
    "max_seconds": 5
  }
}
```

运行中人工介入模拟：

```json
{
  "id": "draft_contract_terms",
  "operation": "merge",
  "agent": {
    "backend": "simulator"
  },
  "simulate": {
    "min_seconds": 8,
    "max_seconds": 12,
    "human_interventions": [
      {
        "request_id": "payment-confirmation",
        "after_seconds": 2,
        "prompt": "请确认是否允许预付款。",
        "assignee_role": "business_owner",
        "schema": {
          "decision": ["allow_advance_payment", "no_advance_payment"],
          "comment": "业务说明"
        }
      }
    ]
  }
}
```

## 7. Human-in-the-loop 节点

预定义人工节点使用 `type: "human"` 或 `type: "approval"`：

```json
{
  "id": "release_approval",
  "label": "人工发布审批",
  "type": "human",
  "operation": "approval",
  "deps": ["release_plan"],
  "prompt": "请审批是否允许发布。",
  "assignee_role": "release_owner",
  "schema": {
    "decision": ["approved", "needs_revision", "rejected"],
    "comment": "审批意见"
  },
  "timeout_seconds": 86400
}
```

Agent 执行过程中临时请求人工输入时，也会复用同一套 human intervention 底层模型；差别只是 intervention 是由 provider 事件产生，而不是任务图里预先写好的节点。

## 8. 常见模式

### 并行分工后汇总

```json
{
  "id": "parallel-research",
  "name": "并行调研后汇总",
  "input": {
    "goal": "调研 10 家竞品最近 6 个月动态"
  },
  "nodes": [
    {
      "id": "product_updates",
      "operation": "agent_task",
      "agent": {
        "backend": "claude",
        "prompt": "调研竞品产品更新。"
      }
    },
    {
      "id": "pricing_changes",
      "operation": "agent_task",
      "agent": {
        "backend": "claude",
        "prompt": "调研竞品价格变化。"
      }
    },
    {
      "id": "final_report",
      "operation": "agent_task",
      "deps": ["product_updates", "pricing_changes"],
      "join": "all",
      "agent": {
        "backend": "claude",
        "prompt": "整合上游结果，输出完整中文报告。"
      }
    }
  ],
  "edges": [
    {"from": "product_updates", "to": "final_report"},
    {"from": "pricing_changes", "to": "final_report"}
  ]
}
```

### 真实 Agent 评审门驱动循环

```json
{
  "id": "review-loop",
  "name": "编码-审查-修订循环",
  "graph_type": "control_flow",
  "start": "implementation",
  "max_total_visits": 10,
  "nodes": [
    {
      "id": "implementation",
      "operation": "agent_task",
      "join": "any",
      "max_visits": 3,
      "deps": ["review_gate", "implementation"],
      "agent": {
        "backend": "claude",
        "artifact_transfer_mode": "inline",
        "prompt": "实现主体代码。若看到上一轮实现和 review_gate 修改意见，请定向修订。"
      }
    },
    {
      "id": "review_gate",
      "operation": "agent_task",
      "join": "any",
      "max_visits": 3,
      "deps": ["implementation"],
      "agent": {
        "backend": "claude",
        "artifact_transfer_mode": "inline",
        "prompt": "严格审查实现。最后输出 JSON：{\"decision\":\"approved 或 needs_revision 或 risk_accepted\",\"reason\":\"原因\",\"required_changes\":[\"修改项\"]}"
      }
    },
    {
      "id": "final_integrator",
      "operation": "agent_task",
      "deps": ["implementation", "review_gate"],
      "agent": {
        "backend": "claude",
        "artifact_transfer_mode": "inline",
        "prompt": "保留实现的公共 API，整合最终交付。"
      }
    }
  ],
  "edges": [
    {"from": "implementation", "to": "review_gate"},
    {
      "from": "review_gate",
      "to": "implementation",
      "when": "last.decision == 'needs_revision' and visits.implementation < 3",
      "label": "继续修订",
      "kind": "loop"
    },
    {
      "from": "review_gate",
      "to": "final_integrator",
      "when": "last.decision in ['approved', 'risk_accepted']",
      "label": "进入最终交付",
      "kind": "branch"
    }
  ]
}
```

### 串行运行避免速率限制

```json
{
  "execution_policy": {
    "mode": "serial",
    "max_concurrent_nodes": 1
  }
}
```

适合 Hermes/Claude 等底层 LLM 服务有 token 速率限制，但你仍希望保留图结构和依赖关系的场景。

### 确定性评测节点

```json
{
  "id": "deterministic_evaluation",
  "operation": "agent_task",
  "agent": {
    "backend": "evaluator",
    "benchmark": "async_ttl_cache_hard_concurrency",
    "single_task_selector": "latest_completed:single-agent",
    "multi_task_selector": "latest_completed:multi-agent"
  }
}
```

`evaluator` provider 不调用 LLM，而是从 Execution Store/Artifact Store 提取候选输出，运行确定性测试并生成结构化报告。

## 9. 编写建议

- 每个真实 Agent 节点的 prompt 只描述该节点业务任务，不要让它创建新的 MAOS 任务图。
- 分支门必须要求稳定结构化输出，至少包含 `decision` 和 `reason`。
- 循环节点必须设置 `max_visits`，整张循环图建议设置 `max_total_visits`。
- 下游需要读取上游结果时，同时写 `deps` 和对应 `edges`，便于执行和 prompt 构建保持一致。
- 大输出默认建议使用 `artifact_transfer_mode: "ref"`；若第三方 Agent 无法访问 artifact API，则使用 `inline`。
- 对速率敏感的图用 `execution_policy.mode=serial`，不要全局降低所有任务并发。
- 使用中文任务时，文件必须保存为 UTF-8 或 UTF-8 with BOM；校验器用 `utf-8-sig` 读取。

## 10. 相关文档

- `docs/task_graph_json_schema.md`
- `docs/api_reference.md`
- `docs/artifact_external_storage.md`
- `docs/human_in_loop_api.md`
- `docs/provider_runtime_lifecycle.md`
- `docs/unified_execution_store.md`

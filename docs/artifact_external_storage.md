# Artifact 外置存储与 A2A 依赖传递

## 存储位置

当前实现使用本机持久目录作为 artifact store：

```text
%MAOS_DATA_DIR%/artifacts
```

如果未设置 `MAOS_DATA_DIR`，默认路径为：

```text
D:\dev\MAOS\temporal-data\artifacts
```

artifact 按内容 SHA-256 去重存储。这个本地实现是对象存储的最小替身，后续可以把 `maos_runtime.a2a.artifact_store` 替换为 S3、MinIO、NAS 或数据库后端，而不改变 A2A 消息格式。

## A2A 消息格式

默认 `ref` 模式下，下游 A2A 消息只携带引用，不内联完整内容：

```json
{
  "payload": {
    "artifact_ref": "sha256-...",
    "uri": "http://127.0.0.1:8766/api/artifacts/sha256-.../content",
    "content_hash": "sha256:...",
    "mime_type": "application/json",
    "size": 12345,
    "summary": "上游输出摘要..."
  }
}
```

## Artifact API

Sandbox API 提供读取接口：

```text
GET /api/artifacts/{artifact_ref}
GET /api/artifacts/{artifact_ref}/content
```

`/content` 返回完整业务依赖内容，并附带 `content_hash`、`mime_type`、`size`，方便下游校验。

## 切换开关

默认模式：

```text
A2A_DEPENDENCY_ARTIFACT_MODE=ref
```

兼容旧的全量注入模式：

```text
A2A_DEPENDENCY_ARTIFACT_MODE=inline
```

也可以在任务图节点或 `agent` 配置中指定：

```json
{
  "agent": {
    "backend": "claude",
    "artifact_transfer_mode": "inline"
  }
}
```

可选值：

- `ref`：A2A 只传 artifact 引用，下游 Agent 通过 artifact API 拉取内容。
- `inline`：A2A 直接内联 compact business result，适合不能访问 artifact API 的 Agent Service。

## 设计边界

外置存储保存的是用于下游协作的业务结果，不保存完整 trace、runs、comments、history 等运行过程数据，避免下游依赖递归膨胀。

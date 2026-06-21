# 持久化多 Agent 编排内核部署方案

本文档说明如何把 `temporal_execution_core` 打包到另一台机器，并部署出当前项目的完整功能。

当前系统由一个持久化执行沙盒微服务、一套 Temporal 执行环境、一个模拟 Agent 服务，以及可选的真实 Agent 适配服务组成。Web 界面只是沙盒 API 的浏览器客户端。

## 1. 目标架构

```text
Browser
  |
  | http://127.0.0.1:8765
  v
execution-api + Web UI
  |
  | starts / connects
  v
temporal-server + execution-worker
  |
  | activity dispatch / signal wakeup
  +---------------------> agent-simulator
  |
  +---------------------> agent-service facade
                           |
                           +--> Multica daemon / Multica CLI
                           +--> Hermes runtime
```

## 2. 需要部署的服务

### 2.1 必需服务

| 服务名 | 默认地址 | 作用 | 启动方式 |
| --- | --- | --- | --- |
| `execution-api` | `http://127.0.0.1:8765` | 创建任务图、查询状态、Web 看板、向 workflow 发 signal | `python sandbox_service.py` |
| `temporal-server` | `127.0.0.1:7233` | 持久 workflow、长等待、重试、查询历史 | 默认由 `execution-api` 内嵌启动 |
| `execution-worker` | 与 `execution-api` 同进程 | 执行 `JsonDagWorkflow` 和 activity | `execution-api` 内部启动 |
| `agent-simulator` | `http://127.0.0.1:8767` | 模拟 Agent 执行，供测试和混合图使用 | `execution-api` 内部启动 |

只运行 simulator 任务图时，上面这些已经足够。

### 2.2 完整真实 Agent 功能需要的服务

| 服务名 | 默认地址/路径 | 作用 |
| --- | --- | --- |
| `agent-service` | `http://127.0.0.1:8091` | 把编排节点请求转成 Multica/Hermes 任务，提供状态与 trace 查询 |
| Multica daemon / CLI | 由 `MULTICA_BIN` 指定 | 真实 Multica Agent 任务创建、状态查询、评论、运行消息 |
| Hermes runtime | 由 `HERMES_BIN` 指定 | 直连 Hermes 节点执行 |

如果目标机器没有 Multica 或 Hermes，仍可以部署最小可跑版，但真实 Agent 节点会失败；需要使用 simulator 示例或把任务图里的 backend 改成 `simulator`。

## 3. 端口与数据文件

| 项目 | 默认值 |
| --- | --- |
| `execution-api` | `8765` |
| `agent-simulator` | `8767` |
| `agent-service` | `8091` |
| Temporal gRPC | `7233` |
| Temporal UI | `8233` |
| Temporal DB | `D:\dev\MAOS\temporal-data\temporal.db` |
| A2A 幂等注册表 | `D:\dev\MAOS\temporal-data\a2a-invocations.json` |

生产或长期运行时，建议把 `temporal-data` 放在单独的数据盘，并定期备份 `temporal.db` 和 `a2a-invocations.json`。

## 4. 打包代码

在源机器上打包项目目录时，不要包含以下内容：

- `.venv/`
- `.env`
- `__pycache__/`
- `*.log`
- `D:\dev\MAOS\temporal-data\`

项目已经提供 `.gitignore`，如果用 Git 迁移代码，通常只需要：

```powershell
git clone <repo-url> temporal_execution_core
```

如果用压缩包迁移，可以压缩当前目录中除上述文件外的内容。

## 5. 目标机器前置条件

### 5.1 Windows 推荐条件

- Windows 10/11 或 Windows Server。
- Python 3.11 或 3.12，且 `python` 命令可用。
- 可以访问 Python 包源，或提前准备好离线 wheel。
- 首次启动内嵌 Temporal dev server 时，Temporal Python SDK 可能需要下载或释放 Temporal server 二进制；离线环境建议先在联网机器预热，或改用外部 Temporal Server。
- 若使用真实 Agent：
  - 已安装并登录 Multica Desktop/daemon。
  - 能找到 `multica.exe`。
  - 已配置 `MULTICA_WORKSPACE_ID`。
  - 若使用 Hermes backend，已安装 Hermes runtime，并能找到 `hermes.cmd` 或 `hermes.exe`。

### 5.2 Linux/macOS 条件

Simulator 与 Sandbox 可以运行；真实 Multica/Hermes 需要目标平台存在对应 runtime。当前默认 `.env.example` 中的 Multica/Hermes 路径是 Windows 路径，迁移到 Unix 时必须改成目标平台路径，或者跳过 Agent Service。

## 6. 一键部署

### 6.1 Windows PowerShell

在目标机器解压项目后执行：

```powershell
cd D:\dev\MAOS\temporal_execution_core
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1
```

仅部署 simulator 能力，不启动真实 Agent Service：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1 -SkipAgentService
```

将 `execution-worker` 从 `execution-api` 中拆成独立进程启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1 `
  -SkipAgentService `
  -SplitWorker
```

生产模式要求连接外部 `temporal-server`，避免误启内嵌 dev server：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1 `
  -SkipAgentService `
  -UseExternalTemporal `
  -TemporalAddress "10.0.0.10:7233" `
  -TemporalNamespace "maos" `
  -SplitWorker `
  -Production
```

指定真实 Agent 运行时路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1 `
  -MulticaBin "C:\Path\To\multica.exe" `
  -MulticaWorkspaceId "<workspace-id>" `
  -HermesBin "D:\dev\MAOS\AgentRuntime\hermes.cmd" `
  -HermesGitBashPath "D:\Program Files\Git\bin\bash.exe"
```

如果端口已有旧进程占用，可以显式要求脚本停止目标端口上的旧进程：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_windows.ps1 -RestartExisting
```

### 6.2 Linux/macOS Bash

```bash
cd /opt/maos/temporal_execution_core
bash scripts/deploy_unix.sh
```

跳过 Agent Service：

```bash
SKIP_AGENT_SERVICE=1 bash scripts/deploy_unix.sh
```

将 `execution-worker` 从 `execution-api` 中拆成独立进程启动：

```bash
SKIP_AGENT_SERVICE=1 SPLIT_WORKER=1 bash scripts/deploy_unix.sh
```

生产模式要求连接外部 `temporal-server`：

```bash
SKIP_AGENT_SERVICE=1 \
USE_EXTERNAL_TEMPORAL=1 \
TEMPORAL_ADDRESS=10.0.0.10:7233 \
TEMPORAL_NAMESPACE=maos \
SPLIT_WORKER=1 \
PRODUCTION_MODE=1 \
bash scripts/deploy_unix.sh
```

## 7. 手动部署命令

```powershell
cd D:\dev\MAOS\temporal_execution_core
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动 `agent-service`：

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.agent_service.main:app --host 127.0.0.1 --port 8091
```

启动 `execution-api`、`agent-simulator` 和内嵌持久 `temporal-server`：

```powershell
.\.venv\Scripts\python.exe sandbox_service.py `
  --host 127.0.0.1 `
  --port 8765 `
  --simulator-host 127.0.0.1 `
  --simulator-port 8767 `
  --temporal-host 127.0.0.1 `
  --temporal-port 7233 `
  --temporal-ui-port 8233 `
  --temporal-db-file D:\dev\MAOS\temporal-data\temporal.db
```

如需让 `execution-worker` 独立运行，先启动不内嵌 worker 的 `execution-api`：

```powershell
.\.venv\Scripts\python.exe sandbox_service.py `
  --host 127.0.0.1 `
  --port 8765 `
  --simulator-host 127.0.0.1 `
  --simulator-port 8767 `
  --temporal-host 127.0.0.1 `
  --temporal-port 7233 `
  --temporal-ui-port 8233 `
  --temporal-db-file D:\dev\MAOS\temporal-data\temporal.db `
  --no-worker
```

再启动独立 `execution-worker`：

```powershell
.\.venv\Scripts\python.exe execution_worker.py `
  --temporal-address 127.0.0.1:7233 `
  --temporal-namespace default
```

## 8. 使用外部 Temporal Server

内嵌 Temporal dev server 适合本地开发和单机验证。更稳定的部署方式是单独部署 `temporal-server`，然后让 `execution-api` 连接它：

```powershell
.\.venv\Scripts\python.exe sandbox_service.py --temporal-address 127.0.0.1:7233
```

外部 `temporal-server` + 独立 `execution-worker` 的推荐组合：

```powershell
.\.venv\Scripts\python.exe sandbox_service.py `
  --temporal-address 127.0.0.1:7233 `
  --temporal-namespace default `
  --no-worker `
  --production

.\.venv\Scripts\python.exe execution_worker.py `
  --temporal-address 127.0.0.1:7233 `
  --temporal-namespace default
```

采用外部 Temporal 时，需要独立维护 `temporal-server`、数据库、备份、监控和 UI。

## 9. 健康检查

部署完成后访问：

```text
http://127.0.0.1:8765/api/livez
http://127.0.0.1:8765/api/readyz
http://127.0.0.1:8765/api/health
http://127.0.0.1:8091/livez
http://127.0.0.1:8091/readyz
http://127.0.0.1:8091/health
http://127.0.0.1:8233
```

`/livez` 只表示进程已启动；`/readyz` 表示运行时和关键依赖已经可用。部署脚本使用 `/readyz` 做就绪检查，`/health` 保持兼容。

其中 `/api/health` 应看到：

```json
{
  "ok": true,
  "service": "persistent-execution-sandbox",
  "runtime": {
    "runtime_status": "ready 或 running",
    "temporal": {
      "mode": "embedded-persistent-dev 或 external",
      "target": "127.0.0.1:7233"
    }
  }
}
```

## 10. 验证任务

打开 Web UI：

```text
http://127.0.0.1:8765/
```

选择示例任务图并运行。

命令行验证：

```powershell
.\.venv\Scripts\python.exe run_dag.py examples\order_processing.json
```

如果真实 `agent-service` 未部署，请优先使用 simulator 示例，例如 `order_processing.json`、`content_pipeline.json` 或批量 simulator 示例。

## 11. 常见问题

### 11.1 `/api/health` 显示 Temporal 启动失败

检查 `7233` 是否被占用；如果被旧进程占用，换端口或用部署脚本的 `-RestartExisting`。

### 11.2 `agent-service` `/health` 失败

通常是 Multica daemon 未启动、未登录、`MULTICA_BIN` 路径不正确，或 `MULTICA_WORKSPACE_ID` 不匹配。

### 11.3 Hermes 节点无法执行

检查 `HERMES_BIN`、`HERMES_WORKDIR`、`HERMES_GIT_BASH_PATH`。在 Windows 上如果 Hermes 依赖 Git Bash，必须配置正确的 Git Bash 路径。

### 11.4 Web 可打开但任务提交失败

检查 Sandbox 日志、Temporal 端口和数据库路径。若使用外部 Temporal，确认 `--temporal-address` 能连接。

## 12. 生产化建议

- 用外部 `temporal-server` 替代内嵌 dev server。
- 用进程管理器托管服务，例如 Windows Task Scheduler、NSSM、systemd 或 Docker。
- 将 `Temporal DB`、`A2A_INVOCATION_REGISTRY_FILE` 和服务日志放到专门的数据目录。
- 对 `agent-service`、`execution-api` 加认证和反向代理，不要直接暴露到公网。
- 为大规模并发配置真实 Temporal 集群、数据库、指标监控和日志采集。

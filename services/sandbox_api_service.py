from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from maos_runtime.a2a.artifact_store import load_artifact
from maos_runtime.a2a.codex_pool import codex_runtime_pool_status
from maos_runtime.sandbox_runtime import (
    DEFAULT_TEMPORAL_PORT,
    ControlFlowTaskService,
)
from maos_runtime.graph.schema import GraphValidationError
from web.sandbox_api_server import list_examples, load_graph
from web.web_agent_api import (
    build_agent_input,
    build_agent_trace,
    build_local_runtime_input,
    build_local_runtime_output,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_TEMPORAL_ADDRESS = f"127.0.0.1:{DEFAULT_TEMPORAL_PORT}"
DEFAULT_SIMULATOR_URL = "http://127.0.0.1:8767"
DEFAULT_EXAMPLES_DIR = "examples"


@dataclass
class SandboxApiConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    temporal_namespace: str = "default"
    simulator_url: str = DEFAULT_SIMULATOR_URL
    examples_dir: Path = Path(DEFAULT_EXAMPLES_DIR).resolve()

    @property
    def sandbox_api_base(self) -> str:
        return f"http://{self.host}:{self.port}"


class TaskBatchRequest(BaseModel):
    graphs: list[dict[str, Any]] = Field(default_factory=list)


class TaskBatchResponse(BaseModel):
    ok: bool
    task_ids: list[str]


class AgentEventResponse(BaseModel):
    ok: bool
    event: dict[str, Any]


class HumanResponseRequest(BaseModel):
    responder: str | None = None
    decision: str | None = None
    comment: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)


class HumanResponseEnvelope(BaseModel):
    ok: bool
    event: dict[str, Any]


def create_app(config: SandboxApiConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="MAOS Persistent Execution Sandbox API",
        version="0.2.0",
        description=(
            "Control-plane API for creating, monitoring, and signaling "
            "Temporal-backed multi-agent control-flow tasks."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config = config or _config_from_env()
    app.state.manager = None

    @app.on_event("startup")
    def _startup() -> None:
        cfg: SandboxApiConfig = app.state.config
        os.environ["SANDBOX_API_BASE"] = cfg.sandbox_api_base
        os.environ["SIMULATOR_API_BASE"] = cfg.simulator_url
        if app.state.manager is None:
            app.state.manager = ControlFlowTaskService(
                temporal_address=cfg.temporal_address,
                temporal_namespace=cfg.temporal_namespace,
                temporal_db_file=None,
                start_worker=False,
            )

    @app.get("/", tags=["system"])
    def service_root() -> dict[str, Any]:
        return {
            "service": "persistent-execution-sandbox-api",
            "docs": "/docs",
            "health": "/api/health",
        }

    @app.get("/api/health", tags=["system"])
    @app.get("/health", tags=["system"], include_in_schema=False)
    def health() -> dict[str, Any]:
        cfg = _config()
        manager = _manager()
        runtime_info = (
            manager.runtime_info()
            if hasattr(manager, "runtime_info")
            else {"runtime_status": "unknown"}
        )
        return {
            "ok": True,
            "service": "persistent-execution-sandbox-api",
            "simulator": cfg.simulator_url,
            "agent_service": os.environ.get("AGENT_SERVICE_API_BASE", "http://127.0.0.1:8091"),
            "runtime": runtime_info,
        }

    @app.get("/api/provider-status", tags=["system"])
    def provider_status() -> dict[str, Any]:
        return {
            "ok": True,
            "providers": {
                "codex": codex_runtime_pool_status(),
            },
        }

    @app.get("/api/tasks", tags=["tasks"])
    @app.get("/state", tags=["tasks"], include_in_schema=False)
    def list_tasks() -> dict[str, Any]:
        return _manager().snapshot()

    @app.post("/api/tasks", response_model=TaskBatchResponse, tags=["tasks"], status_code=201)
    @app.post("/run-batch", response_model=TaskBatchResponse, tags=["tasks"], include_in_schema=False, status_code=201)
    def create_tasks(payload: TaskBatchRequest | list[dict[str, Any]] = Body(...)) -> dict[str, Any]:
        graphs = _graphs_from_payload(payload)
        try:
            task_ids = _manager().submit_batch(graphs)
        except GraphValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Task graph validation failed",
                    "issues": [
                        {"path": issue.path, "message": issue.message}
                        for issue in exc.issues
                    ],
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "task_ids": task_ids}

    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: str) -> dict[str, Any]:
        try:
            return _manager().task_snapshot(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc

    @app.post("/api/agent-callbacks", response_model=AgentEventResponse, tags=["events"])
    @app.post("/api/v1/agent-events", response_model=AgentEventResponse, tags=["events"])
    def agent_callback(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            event = _manager().handle_agent_callback(payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "event": event}

    @app.get("/api/human-interventions", tags=["human-in-the-loop"])
    def list_human_interventions(
        task_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            items = _manager().human_interventions(task_id=task_id, status=status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"human_interventions": items}

    @app.get("/api/tasks/{task_id}/human-interventions", tags=["human-in-the-loop"])
    def task_human_interventions(
        task_id: str,
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            items = _manager().human_interventions(task_id=task_id, status=status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"human_interventions": items}

    @app.post(
        "/api/tasks/{task_id}/human-interventions/{intervention_id}/responses",
        response_model=HumanResponseEnvelope,
        tags=["human-in-the-loop"],
    )
    def resolve_human_intervention(
        task_id: str,
        intervention_id: str,
        payload: HumanResponseRequest,
    ) -> dict[str, Any]:
        response = dict(payload.response or {})
        if payload.decision is not None:
            response.setdefault("decision", payload.decision)
        if payload.comment is not None:
            response.setdefault("comment", payload.comment)
        event_payload = {
            "responder": payload.responder,
            "decision": payload.decision or response.get("decision"),
            "comment": payload.comment or response.get("comment"),
            "response": response,
        }
        try:
            event = _manager().submit_human_response(task_id, intervention_id, event_payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "event": event}

    @app.get("/api/examples", tags=["examples"])
    @app.get("/examples", tags=["examples"], include_in_schema=False)
    def examples() -> dict[str, Any]:
        return {"examples": list_examples(_config().examples_dir)}

    @app.get("/api/example", tags=["examples"])
    @app.get("/example", tags=["examples"], include_in_schema=False)
    def example(name: str = Query(..., min_length=1)) -> dict[str, Any]:
        cfg = _config()
        example_path = (cfg.examples_dir / name).resolve()
        if (
            example_path.parent != cfg.examples_dir.resolve()
            or example_path.suffix.lower() != ".json"
            or not example_path.exists()
        ):
            raise HTTPException(status_code=404, detail=f"Example not found: {name}")
        return load_graph(example_path)

    @app.get("/api/agent-trace", tags=["agent-debug"])
    @app.get("/agent-trace", tags=["agent-debug"], include_in_schema=False)
    def agent_trace(task_id: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return build_agent_trace(task_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/agent-input", tags=["agent-debug"])
    @app.get("/agent-input", tags=["agent-debug"], include_in_schema=False)
    def agent_input(task_id: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return build_agent_input(task_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/local-runtime-input", tags=["agent-debug"])
    @app.get("/local-runtime-input", tags=["agent-debug"], include_in_schema=False)
    def local_runtime_input(
        task_id: str = Query(..., min_length=1),
        backend: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        try:
            return build_local_runtime_input(task_id, backend)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/local-runtime-output", tags=["agent-debug"])
    @app.get("/local-runtime-output", tags=["agent-debug"], include_in_schema=False)
    def local_runtime_output(
        task_id: str = Query(..., min_length=1),
        backend: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        try:
            return build_local_runtime_output(task_id, backend)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/artifacts/{artifact_ref}", tags=["artifacts"])
    def artifact_metadata(artifact_ref: str) -> dict[str, Any]:
        try:
            return {"ok": True, "artifact": load_artifact(artifact_ref)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_ref}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/artifacts/{artifact_ref}/content", tags=["artifacts"])
    def artifact_content(artifact_ref: str) -> dict[str, Any]:
        try:
            artifact = load_artifact(artifact_ref, include_content=True)
            return {
                "ok": True,
                "artifact_ref": artifact.get("artifact_ref"),
                "content_hash": artifact.get("content_hash"),
                "mime_type": artifact.get("mime_type"),
                "size": artifact.get("size"),
                "content": artifact.get("content"),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_ref}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _manager() -> ControlFlowTaskService:
        manager = app.state.manager
        if manager is None:
            raise HTTPException(status_code=503, detail="Sandbox runtime is not ready")
        return manager

    def _config() -> SandboxApiConfig:
        return app.state.config

    return app


def _graphs_from_payload(payload: TaskBatchRequest | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(payload, TaskBatchRequest):
        graphs = payload.graphs
    else:
        graphs = payload
    graphs = _flatten_graph_payloads(graphs)
    if not graphs:
        raise HTTPException(status_code=400, detail="No graphs submitted")
    return graphs


def _flatten_graph_payloads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    graphs: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("graphs"), list):
            graphs.extend(
                graph
                for graph in item["graphs"]
                if isinstance(graph, dict)
            )
        elif isinstance(item, dict):
            graphs.append(item)
    return graphs


def _config_from_env() -> SandboxApiConfig:
    return SandboxApiConfig(
        host=os.environ.get("SANDBOX_API_HOST", DEFAULT_HOST),
        port=int(os.environ.get("SANDBOX_API_PORT", str(DEFAULT_PORT))),
        temporal_address=os.environ.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
        temporal_namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        simulator_url=os.environ.get("SIMULATOR_API_BASE", DEFAULT_SIMULATOR_URL),
        examples_dir=Path(os.environ.get("SANDBOX_EXAMPLES_DIR", DEFAULT_EXAMPLES_DIR)).resolve(),
    )


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the FastAPI sandbox HTTP API without a Web UI or Temporal worker"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--temporal-address", default=DEFAULT_TEMPORAL_ADDRESS)
    parser.add_argument("--temporal-namespace", default="default")
    parser.add_argument("--simulator-url", default=DEFAULT_SIMULATOR_URL)
    parser.add_argument("--examples-dir", default=DEFAULT_EXAMPLES_DIR)
    args = parser.parse_args()

    config = SandboxApiConfig(
        host=args.host,
        port=args.port,
        temporal_address=args.temporal_address,
        temporal_namespace=args.temporal_namespace,
        simulator_url=args.simulator_url,
        examples_dir=Path(args.examples_dir).resolve(),
    )
    app.state.config = config
    os.environ["SANDBOX_API_BASE"] = config.sandbox_api_base
    os.environ["SIMULATOR_API_BASE"] = config.simulator_url

    print(f"Sandbox API running at {config.sandbox_api_base}")
    print(f"OpenAPI docs: {config.sandbox_api_base}/docs")
    print(f"Temporal server: external {config.temporal_address}")
    print(f"Simulator API: {config.simulator_url}")
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()

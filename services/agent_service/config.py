from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MULTICA_BIN = (
    r"C:\Users\DWG\AppData\Local\Programs\@multicadesktop"
    r"\resources\app.asar.unpacked\resources\bin\multica.exe"
)

DEFAULT_HERMES_BIN = r"D:\dev\MAOS\AgentRuntime\hermes.cmd"
DEFAULT_HERMES_GIT_BASH = r"D:\Program Files\Git\bin\bash.exe"

DEFAULT_AGENT_PRESETS = {
    "general_chat": {
        "id": "caf819e8-3d87-4ba4-90fc-0a57b9360dc1",
        "name": "通用聊天助手",
    },
    "product_strategy": {
        "id": "c2b17a27-5718-41ad-b964-144297c384b2",
        "name": "产品策略师",
    },
    "architect": {
        "id": "ab114b72-895d-47a1-897b-d6f48c9609c2",
        "name": "系统架构师",
    },
    "implementer": {
        "id": "b9f67edc-8076-4c1c-b758-6f065c53a132",
        "name": "代码实现工程师",
    },
    "qa": {
        "id": "154fa39e-783c-4fb5-8be0-b2947878702e",
        "name": "测试与质量工程师",
    },
    "security": {
        "id": "b2360e13-c69e-4efd-baa5-317e6f4a2818",
        "name": "安全审查员",
    },
    "data": {
        "id": "e2838dfe-548e-474b-8cd7-949221a85ab3",
        "name": "数据分析师",
    },
    "devops": {
        "id": "45a585aa-7b42-4f18-b9be-d71901826678",
        "name": "DevOps运维工程师",
    },
    "docs": {
        "id": "be3b2e70-fb98-410f-8457-e0614464fff2",
        "name": "技术文档撰写员",
    },
    "research": {
        "id": "90fe4a92-f2b4-4809-8047-f3623e2ee95a",
        "name": "研究情报员",
    },
    "coordinator": {
        "id": "933fbc7c-763c-42aa-a721-d7423afb43de",
        "name": "项目协调员",
    },
}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    multica_bin: str
    multica_profile: str | None
    multica_workspace_id: str | None
    multica_workspaces_root: str | None
    hermes_bin: str
    hermes_workdir: str
    hermes_git_bash_path: str | None
    poll_seconds: float
    command_timeout_seconds: float
    hermes_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        _load_dotenv(project_root / ".env")

        return cls(
            multica_bin=os.getenv("MULTICA_BIN", DEFAULT_MULTICA_BIN),
            multica_profile=os.getenv("MULTICA_PROFILE", "desktop-api.multica.ai"),
            multica_workspace_id=os.getenv(
                "MULTICA_WORKSPACE_ID",
                "59c18628-a73e-461d-b8aa-138d507a5e55",
            ),
            multica_workspaces_root=os.getenv("MULTICA_WORKSPACES_ROOT"),
            hermes_bin=os.getenv("HERMES_BIN", DEFAULT_HERMES_BIN),
            hermes_workdir=os.getenv("HERMES_WORKDIR", str(project_root)),
            hermes_git_bash_path=os.getenv("HERMES_GIT_BASH_PATH", DEFAULT_HERMES_GIT_BASH),
            poll_seconds=float(os.getenv("AGENT_SERVICE_POLL_SECONDS", "2")),
            command_timeout_seconds=float(
                os.getenv("AGENT_SERVICE_COMMAND_TIMEOUT_SECONDS", "60")
            ),
            hermes_timeout_seconds=float(os.getenv("AGENT_SERVICE_HERMES_TIMEOUT_SECONDS", "600")),
        )

"""兼容入口：启动 Multica 适配微服务。

推荐新命令：
    python -m uvicorn services.agent_service.main:app --host 127.0.0.1 --port 8091
"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "services.agent_service.main:app",
        host="127.0.0.1",
        port=8091,
        reload=False,
    )


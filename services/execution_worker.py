from __future__ import annotations

import argparse
import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from maos_runtime.dag_workflow import ACTIVITIES, JsonDagWorkflow
from maos_runtime.sandbox_runtime import TASK_QUEUE


DEFAULT_TEMPORAL_ADDRESS = "127.0.0.1:7233"
DEFAULT_TEMPORAL_NAMESPACE = "default"


async def run_worker(
    *,
    temporal_address: str,
    temporal_namespace: str,
    task_queue: str,
) -> None:
    client = await Client.connect(
        temporal_address,
        namespace=temporal_namespace,
    )
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[JsonDagWorkflow],
        activities=ACTIVITIES,
    ):
        print(
            "execution-worker running: "
            f"temporal={temporal_address}, namespace={temporal_namespace}, "
            f"task_queue={task_queue}"
        )
        while True:
            await asyncio.sleep(3600)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Temporal execution-worker for JSON control-flow tasks."
    )
    parser.add_argument(
        "--temporal-address",
        default=os.environ.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
        help="Temporal frontend address, for example 127.0.0.1:7233.",
    )
    parser.add_argument(
        "--temporal-namespace",
        default=os.environ.get("TEMPORAL_NAMESPACE", DEFAULT_TEMPORAL_NAMESPACE),
    )
    parser.add_argument(
        "--task-queue",
        default=os.environ.get("TEMPORAL_TASK_QUEUE", TASK_QUEUE),
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            run_worker(
                temporal_address=args.temporal_address,
                temporal_namespace=args.temporal_namespace,
                task_queue=args.task_queue,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

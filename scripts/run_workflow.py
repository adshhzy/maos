import asyncio
import uuid

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from scripts.workflows import GreetingInput, GreetingWorkflow, compose_greeting


TASK_QUEUE = "hello-temporal-task-queue"


async def main() -> None:
    async with await WorkflowEnvironment.start_local() as env:
        client: Client = env.client

        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[GreetingWorkflow],
            activities=[compose_greeting],
        ):
            result = await client.execute_workflow(
                GreetingWorkflow.run,
                GreetingInput(name="MAOS"),
                id=f"hello-temporal-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )

            print(result)


if __name__ == "__main__":
    asyncio.run(main())

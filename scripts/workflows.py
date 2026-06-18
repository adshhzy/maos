from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow


@dataclass
class GreetingInput:
    name: str


@activity.defn
async def compose_greeting(name: str) -> str:
    return f"Hello, {name}! Temporal workflow completed successfully."


@workflow.defn
class GreetingWorkflow:
    @workflow.run
    async def run(self, greeting_input: GreetingInput) -> str:
        return await workflow.execute_activity(
            compose_greeting,
            greeting_input.name,
            start_to_close_timeout=timedelta(seconds=10),
        )

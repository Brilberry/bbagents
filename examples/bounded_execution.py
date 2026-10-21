"""A timeout is an uncertain outcome, not permission to repeat an effect."""

import asyncio

from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.runtime import Action


async def wait(action, context):
    await asyncio.sleep(1)


def run_example() -> None:
    runtime = AsyncActionRuntime(AsyncFunctionAdapter({"wait": wait}))
    result = asyncio.run(runtime.execute(Action("wait", timeout_seconds=0.01)))
    assert result.outcome_unknown and result.attempts == 1
    print(result.status.value, "reconciliation required")


if __name__ == "__main__":
    run_example()

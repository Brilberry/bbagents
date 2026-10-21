import asyncio
from pathlib import Path
import sys

import pytest

from bbagents.execution import (
    AsyncActionRuntime,
    AsyncFunctionAdapter,
    ExecutionFailure,
    SafeRetryPolicy,
    SubprocessAdapter,
)
from bbagents.runtime import Action, ActionStatus, FailureKind


def test_deadline_cancels_cooperative_handler_without_retry() -> None:
    cancelled = []

    async def handler(action, context):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    result = asyncio.run(
        AsyncActionRuntime(AsyncFunctionAdapter({"wait": handler})).execute(
            Action("wait", timeout_seconds=0.01, retry_safe=True)
        )
    )
    assert result.status == ActionStatus.TIMED_OUT and result.outcome_unknown
    assert result.attempts == 1 and cancelled == [True]


def test_retry_requires_transient_failure_and_explicit_safety() -> None:
    calls = []

    async def handler(action, context):
        calls.append(1)
        if len(calls) == 1:
            raise ExecutionFailure("busy", FailureKind.TRANSIENT)
        return "ok"

    runtime = AsyncActionRuntime(
        AsyncFunctionAdapter({"read": handler}), retry_policy=SafeRetryPolicy(2)
    )
    assert not asyncio.run(runtime.execute(Action("read"))).success
    calls.clear()
    result = asyncio.run(runtime.execute(Action("read", retry_safe=True)))
    assert result.success and result.attempts == 2


def test_subprocess_timeout_terminates_child_before_effect(tmp_path: Path) -> None:
    marker = tmp_path / "late"
    adapter = SubprocessAdapter(
        {
            "late": (
                sys.executable,
                "-c",
                f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()",
            )
        }
    )
    result = asyncio.run(
        AsyncActionRuntime(adapter).execute(Action("late", timeout_seconds=0.02))
    )
    assert result.status == ActionStatus.TIMED_OUT
    assert not marker.exists()


def test_external_cancellation_propagates() -> None:
    async def handler(action, context):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            AsyncActionRuntime(AsyncFunctionAdapter({"x": handler})).execute(
                Action("x")
            )
        )

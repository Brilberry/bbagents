"""Bounded async execution; adapters must cooperate with task cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Any, Awaitable, Callable, Mapping, Protocol

from .runtime import (
    Action,
    ActionPolicy,
    ActionResult,
    ActionStatus,
    AllowAllPolicy,
    FailureKind,
    _evaluate_policy,
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    deadline: float

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check_deadline(self) -> None:
        if self.remaining_seconds <= 0:
            raise TimeoutError("execution deadline expired")


class ExecutionAdapter(Protocol):
    async def execute(self, action: Action, context: ExecutionContext) -> Any: ...


class ExecutionFailure(Exception):
    def __init__(
        self, message: str, kind: FailureKind, *, outcome_unknown: bool = False
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.outcome_unknown = outcome_unknown


class RetryPolicy(Protocol):
    def should_retry(self, action: Action, result: ActionResult) -> bool: ...


@dataclass(frozen=True, slots=True)
class SafeRetryPolicy:
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    def should_retry(self, action: Action, result: ActionResult) -> bool:
        return (
            action.retry_safe
            and not result.outcome_unknown
            and result.failure_kind == FailureKind.TRANSIENT
            and result.attempts < self.max_attempts
        )


AsyncHandler = Callable[[Action, ExecutionContext], Awaitable[Any]]


class AsyncFunctionAdapter:
    """Handlers must not block the event loop or suppress cancellation."""

    def __init__(self, handlers: Mapping[str, AsyncHandler]) -> None:
        self.handlers = dict(handlers)

    async def execute(self, action: Action, context: ExecutionContext) -> Any:
        if action.name not in self.handlers:
            raise ExecutionFailure("Unknown action", FailureKind.VALIDATION)
        return await self.handlers[action.name](action, context)


class SubprocessAdapter:
    """Run application-allowlisted commands without a shell; kill/reap on cancel.

    This is process control, not an OS security sandbox. Commands must bound their
    output and manage descendants; external side effects can survive termination.
    """

    def __init__(self, commands: Mapping[str, tuple[str, ...]]) -> None:
        self.commands = dict(commands)

    async def execute(self, action: Action, context: ExecutionContext) -> str:
        if action.name not in self.commands or action.parameters:
            raise ExecutionFailure("Command not allowlisted", FailureKind.VALIDATION)
        context.check_deadline()
        process = await asyncio.create_subprocess_exec(
            *self.commands[action.name],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ExecutionFailure(
                stderr.decode(errors="replace"),
                FailureKind.PERMANENT,
                outcome_unknown=True,
            )
        return stdout.decode(errors="replace")


class AsyncActionRuntime:
    def __init__(
        self,
        adapter: ExecutionAdapter,
        *,
        policy: ActionPolicy | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.adapter = adapter
        self.policy = policy or AllowAllPolicy()
        self.retry_policy = retry_policy or SafeRetryPolicy()

    async def execute(self, action: Action) -> ActionResult:
        if not math.isfinite(action.timeout_seconds) or action.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        started = time.monotonic()
        context = ExecutionContext(started + action.timeout_seconds)
        attempt = 0
        while True:
            decision = _evaluate_policy(self.policy, action)
            if not decision.allowed:
                return ActionResult(
                    ActionStatus.PENDING_INTERVENTION
                    if decision.pending
                    else ActionStatus.DENIED,
                    error=decision.reason,
                    attempts=attempt,
                    intervention_request_id=decision.intervention_request_id,
                )
            attempt += 1
            try:
                async with asyncio.timeout(context.remaining_seconds):
                    context.check_deadline()
                    output = await self.adapter.execute(action, context)
                    context.check_deadline()
                return ActionResult(
                    ActionStatus.SUCCEEDED,
                    output,
                    attempts=attempt,
                    duration_seconds=time.monotonic() - started,
                )
            except TimeoutError as exc:
                result = ActionResult(
                    ActionStatus.TIMED_OUT,
                    error=str(exc) or "Deadline exceeded",
                    attempts=attempt,
                    failure_kind=FailureKind.TIMEOUT,
                    outcome_unknown=True,
                )
            except ExecutionFailure as exc:
                result = ActionResult(
                    ActionStatus.FAILED,
                    error=str(exc),
                    attempts=attempt,
                    failure_kind=exc.kind,
                    outcome_unknown=exc.outcome_unknown,
                )
            except Exception as exc:
                result = ActionResult(
                    ActionStatus.FAILED,
                    error=str(exc),
                    attempts=attempt,
                    failure_kind=FailureKind.UNKNOWN,
                    outcome_unknown=True,
                )
            result = _duration(result, time.monotonic() - started)
            if not self.retry_policy.should_retry(action, result):
                return result


def _duration(result: ActionResult, seconds: float) -> ActionResult:
    from dataclasses import replace

    return replace(result, duration_seconds=seconds)

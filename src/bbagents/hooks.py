"""Bounded, traced async enforcement boundaries with explicit failure behavior."""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Awaitable, Callable, Mapping

from .core import NullTrace, TraceSink


class HookBoundary(str, Enum):
    BEFORE_MODEL = "before_model"
    BEFORE_ACTION = "before_action"
    AFTER_RESULT = "after_result"
    BEFORE_COMPLETE = "before_complete"


@dataclass(frozen=True, slots=True)
class HookContext:
    boundary: HookBoundary
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Hook:
    name: str
    boundary: HookBoundary
    callback: Callable[[HookContext], Awaitable[HookDecision]]
    timeout_seconds: float = 5.0
    blocking: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("hook timeout must be finite and positive")
        if not self.blocking and self.boundary != HookBoundary.AFTER_RESULT:
            raise ValueError("Only observational AFTER_RESULT hooks may fail open")


class HookRejected(RuntimeError):
    pass


class HookRunner:
    """Callbacks must cooperate with cancellation; hooks confer no capabilities."""

    def __init__(
        self, hooks: tuple[Hook, ...] = (), *, trace: TraceSink | None = None
    ) -> None:
        self.hooks = hooks
        self.trace = trace or NullTrace()

    async def run(self, context: HookContext) -> None:
        for hook in self.hooks:
            if hook.boundary != context.boundary:
                continue
            self.trace.record(
                "hook.started", {"hook": hook.name, "boundary": context.boundary.value}
            )
            try:
                async with asyncio.timeout(hook.timeout_seconds):
                    decision = await hook.callback(context)
            except Exception as exc:
                self.trace.record("hook.failed", {"hook": hook.name, "error": str(exc)})
                if hook.blocking:
                    raise HookRejected(f"Hook {hook.name} failed") from exc
                continue
            self.trace.record(
                "hook.finished", {"hook": hook.name, "allowed": decision.allowed}
            )
            if not decision.allowed:
                raise HookRejected(f"Hook {hook.name}: {decision.reason}")

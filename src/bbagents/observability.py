"""Portable typed trace events, spans, redaction, and basic evaluation."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4


class TraceEventName(str, Enum):
    AGENT_RUN_STARTED = "agent.run.started"
    AGENT_RUN_FINISHED = "agent.run.finished"
    DECISION_STARTED = "decision.started"
    DECISION_COMPLETED = "decision.completed"
    DECISION_FAILED = "decision.failed"
    BEHAVIOR_POLICY_EVALUATED = "behavior.policy_evaluated"
    BEHAVIOR_SELECTED = "behavior.selected"
    BEHAVIOR_NO_MATCH = "behavior.no_match"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    SPAN_STARTED = "span.started"
    SPAN_FINISHED = "span.finished"


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Correlation identifiers portable across tracing backends."""

    run_id: str = field(default_factory=lambda: uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid4().hex)
    parent_span_id: str | None = None

    def child(self) -> "TraceContext":
        return TraceContext(run_id=self.run_id, parent_span_id=self.span_id)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """A trace envelope with standard correlation fields and extensible attributes."""

    name: TraceEventName | str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    context: TraceContext = field(default_factory=TraceContext)

    @property
    def payload(self) -> Mapping[str, Any]:
        """Compatibility alias for pre-0.2 trace consumers."""

        return self.attributes

    @property
    def run_id(self) -> str:
        return self.context.run_id

    @property
    def span_id(self) -> str:
        return self.context.span_id

    @property
    def parent_span_id(self) -> str | None:
        return self.context.parent_span_id


class TraceRedactor(Protocol):
    def redact(self, attributes: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class KeyRedactor:
    """Redact sensitive keys recursively before a trace leaves application memory."""

    keys: frozenset[str] = frozenset(
        {"authorization", "password", "secret", "token", "api_key"}
    )
    replacement: str = "[REDACTED]"

    def redact(self, attributes: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            key: self._redact_value(key, value)
            for key, value in attributes.items()
        }

    def _redact_value(self, key: str, value: Any) -> Any:
        if key.casefold() in self.keys:
            return self.replacement
        if isinstance(value, Mapping):
            return self.redact(value)
        if isinstance(value, list):
            return [self._redact_value("", item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value("", item) for item in value)
        return value


class InMemoryTrace:
    def __init__(
        self,
        context: TraceContext | None = None,
        *,
        redactor: TraceRedactor | None = None,
    ) -> None:
        self.events: list[TraceEvent] = []
        self.context = context or TraceContext()
        self.redactor = redactor

    def record(
        self,
        event: TraceEventName | str | TraceEvent,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(event, TraceEvent):
            self.events.append(self._redact_event(event))
            return
        self.events.append(
            self._event(event, payload or {}, context=self.context)
        )

    def child(self) -> "InMemoryTrace":
        """Create a correlated trace for a nested agent or orchestration node."""

        return InMemoryTrace(self.context.child(), redactor=self.redactor)

    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> "InMemorySpan":
        return InMemorySpan(self, name, attributes or {})

    def events_named(self, name: TraceEventName | str) -> list[TraceEvent]:
        name_value = _event_name(name)
        return [
            event
            for event in self.events
            if _event_name(event.name) == name_value
        ]

    def _event(
        self,
        name: TraceEventName | str,
        attributes: Mapping[str, Any],
        *,
        context: TraceContext,
    ) -> TraceEvent:
        return self._redact_event(
            TraceEvent(_event_name(name), dict(attributes), context=context)
        )

    def _redact_event(self, event: TraceEvent) -> TraceEvent:
        if self.redactor is None:
            return event
        return TraceEvent(
            event.name,
            self.redactor.redact(event.attributes),
            timestamp=event.timestamp,
            context=event.context,
        )


class InMemorySpan(AbstractContextManager[TraceContext]):
    def __init__(
        self,
        trace: InMemoryTrace,
        name: str,
        attributes: Mapping[str, Any],
    ) -> None:
        self.trace = trace
        self.name = name
        self.attributes = attributes
        self.context = trace.context.child()

    def __enter__(self) -> TraceContext:
        self.trace.events.append(
            self.trace._event(
                TraceEventName.SPAN_STARTED,
                {"name": self.name, **self.attributes},
                context=self.context,
            )
        )
        return self.context

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        attributes: dict[str, Any] = {"name": self.name}
        if exc is not None:
            attributes["error"] = str(exc)
        self.trace.events.append(
            self.trace._event(
                TraceEventName.SPAN_FINISHED,
                attributes,
                context=self.context,
            )
        )
        return None


@dataclass(frozen=True, slots=True)
class Evaluation:
    passed: bool
    score: float
    reason: str = ""


def exact_match(actual: str, expected: str) -> Evaluation:
    passed = actual.strip() == expected.strip()
    return Evaluation(
        passed,
        float(passed),
        "exact match" if passed else "values differ",
    )


def _event_name(name: TraceEventName | str) -> str:
    return name.value if isinstance(name, TraceEventName) else name

"""Provider-neutral structured model decisions with validation and tracing."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

from .context import Context
from .core import LanguageModel, NullTrace, TraceSink
from .observability import TraceEventName

T = TypeVar("T")
SchemaT = TypeVar("SchemaT", covariant=True)


@dataclass(frozen=True, slots=True)
class DecisionRequirements:
    """Quality and latency constraints used by an application model selector."""

    minimum_quality: float = 0.0
    maximum_latency_seconds: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_quality <= 1:
            raise ValueError("minimum_quality must be between 0 and 1")
        if (
            self.maximum_latency_seconds is not None
            and self.maximum_latency_seconds <= 0
        ):
            raise ValueError("maximum_latency_seconds must be positive")


class DecisionSchema(Protocol[SchemaT]):
    """Parse and validate provider output without imposing a schema dependency."""

    @property
    def name(self) -> str: ...

    @property
    def provider_format(self) -> Any | None: ...

    def parse(self, value: Any) -> SchemaT: ...


@dataclass(frozen=True, slots=True)
class PythonTypeSchema(Generic[T]):
    """Schema adapter for ordinary Python runtime types and dataclasses."""

    value_type: type[T]

    @property
    def name(self) -> str:
        return self.value_type.__name__

    @property
    def provider_format(self) -> type[T] | None:
        return None if self.value_type is str else self.value_type

    def parse(self, value: Any) -> T:
        if not isinstance(value, self.value_type):
            raise TypeError(
                "Model returned "
                f"{type(value).__name__}, expected {self.value_type.__name__}"
            )
        return value


@dataclass(frozen=True, slots=True)
class DecisionUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class DecisionOutput(Generic[T]):
    """Provider adapter output with optional portable execution metadata."""

    value: T
    model_id: str | None = None
    usage: DecisionUsage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionRequest(Generic[T]):
    """A bounded request with an explicit output schema and application validator."""

    task: str
    system: str
    context: Context
    schema: DecisionSchema[T] | type[T]
    requirements: DecisionRequirements = field(default_factory=DecisionRequirements)
    validate: Callable[[T], bool] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def output_schema(self) -> DecisionSchema[T]:
        return _coerce_schema(self.schema)


@dataclass(frozen=True, slots=True)
class DecisionResult(Generic[T]):
    value: T
    attempts: int
    duration_seconds: float
    model_id: str | None = None
    usage: DecisionUsage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DecisionModel(Protocol):
    """Produces a proposal; it does not execute application actions."""

    def decide(self, request: DecisionRequest[T]) -> T | DecisionOutput[T]: ...


class DecisionFailure(RuntimeError):
    """Raised after an invalid or failed model decision exhausts its retries."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"Decision failed after {attempts} attempts: {last_error}")


class LanguageModelDecisionAdapter:
    """Adapt the package's minimal completion protocol to a decision model."""

    def __init__(
        self, model: LanguageModel, *, model_id: str | None = None
    ) -> None:
        self.model = model
        self.model_id = model_id

    def decide(self, request: DecisionRequest[T]) -> DecisionOutput[Any]:
        value = self.model.complete(
            request.system,
            _render_user_message(request),
            response_format=request.output_schema().provider_format,
        )
        return DecisionOutput(value, model_id=self.model_id)


class DecisionRuntime:
    """Invoke a model with schema validation, retries, and portable metadata."""

    def __init__(
        self,
        model: DecisionModel,
        *,
        trace: TraceSink | None = None,
        model_id: str | None = None,
    ) -> None:
        self.model = model
        self.trace = trace or NullTrace()
        self.model_id = model_id

    def decide(
        self, request: DecisionRequest[T], *, retries: int = 0
    ) -> DecisionResult[T]:
        started = time.monotonic()
        schema = request.output_schema()
        self.trace.record(
            TraceEventName.DECISION_STARTED,
            {
                "task": request.task,
                "schema": schema.name,
                "context_item_count": len(request.context.items),
                "context_omitted_items": request.context.omitted_items,
                **dict(request.metadata),
            },
        )
        error: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                output = _coerce_output(self.model.decide(request))
                value = schema.parse(output.value)
                if request.validate is not None and not request.validate(value):
                    raise ValueError("Decision failed application validation")
                result = DecisionResult(
                    value,
                    attempt,
                    time.monotonic() - started,
                    model_id=output.model_id or self.model_id,
                    usage=output.usage,
                    metadata=output.metadata,
                )
                self.trace.record(
                    TraceEventName.DECISION_COMPLETED,
                    {
                        "task": request.task,
                        "attempts": attempt,
                        "model_id": result.model_id,
                        **dict(request.metadata),
                    },
                )
                return result
            except Exception as exc:
                error = exc
                self.trace.record(
                    TraceEventName.DECISION_FAILED,
                    {
                        "task": request.task,
                        "attempt": attempt,
                        "error": str(exc),
                        **dict(request.metadata),
                    },
                )
        assert error is not None
        raise DecisionFailure(retries + 1, error) from error


def _coerce_schema(schema: DecisionSchema[T] | type[T]) -> DecisionSchema[T]:
    if isinstance(schema, type):
        return PythonTypeSchema(schema)
    return schema


def _coerce_output(value: T | DecisionOutput[T]) -> DecisionOutput[T]:
    return value if isinstance(value, DecisionOutput) else DecisionOutput(value)


def _render_user_message(request: DecisionRequest[Any]) -> str:
    return f"Task:\n{request.task}\n\nContext:\n{request.context.content}"

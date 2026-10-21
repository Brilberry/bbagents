"""Shared value objects and provider-neutral interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, TypeVar

from .provenance import Provenance

T = TypeVar("T")


@dataclass(slots=True)
class Observation:
    """A fact returned by a tool or runtime component."""

    source: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = field(
        default_factory=lambda: Provenance.system("runtime")
    )


class LanguageModel(Protocol):
    """Minimal structured-completion interface adapted by the decision runtime."""

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_format: Any | None = None,
    ) -> T | str: ...


class TraceSink(Protocol):
    """Receives structured execution events."""

    def record(
        self, event: Any, payload: Mapping[str, Any] | None = None
    ) -> None: ...


class NullTrace:
    """Trace sink for callers that do not need telemetry."""

    def record(self, event: Any, payload: Mapping[str, Any] | None = None) -> None:
        del event, payload

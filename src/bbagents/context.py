"""Budgeted context assembly and state-projection contracts.

Applications decide which domain facts matter. This module provides the
portable mechanics for selecting, truncating, and optionally summarizing those
facts before they are passed to a model or another agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, TypeVar

from .provenance import Provenance

StateT = TypeVar("StateT", contravariant=True)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Limits applied when assembling an execution context."""

    max_items: int = 20
    max_characters: int = 12_000

    def __post_init__(self) -> None:
        if self.max_items < 1:
            raise ValueError("max_items must be positive")
        if self.max_characters < 1:
            raise ValueError("max_characters must be positive")


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One application-provided candidate for a model context."""

    content: str
    source: str
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = field(
        default_factory=lambda: Provenance.system("context")
    )


@dataclass(frozen=True, slots=True)
class Context:
    """A bounded, attributable view of application state."""

    content: str
    items: tuple[ContextItem, ...]
    omitted_items: int = 0
    summarized: bool = False
    summary_sources: tuple[ContextItem, ...] = ()


class StateProjector(Protocol[StateT]):
    """Maps application state into ranked, attributable context candidates."""

    def project(self, state: StateT) -> Sequence[ContextItem]: ...


class ContextSummarizer(Protocol):
    """Compresses omitted context using an application-selected implementation."""

    def summarize(
        self,
        items: Sequence[ContextItem],
        *,
        max_characters: int,
    ) -> str: ...


class ContextBuilder:
    """Deterministically assembles a bounded context from ranked candidates."""

    def __init__(
        self,
        budget: ContextBudget,
        *,
        summarizer: ContextSummarizer | None = None,
    ) -> None:
        self.budget = budget
        self.summarizer = summarizer

    def build(self, items: Sequence[ContextItem]) -> Context:
        ranked = sorted(enumerate(items), key=lambda pair: (-pair[1].priority, pair[0]))
        selected: list[ContextItem] = []
        omitted: list[ContextItem] = []
        used = 0
        for _, item in ranked:
            separator = 1 if selected else 0
            if (
                len(selected) < self.budget.max_items
                and used + separator + len(item.content) <= self.budget.max_characters
            ):
                selected.append(item)
                used += separator + len(item.content)
            else:
                omitted.append(item)

        content = "\n".join(item.content for item in selected)
        if not omitted or self.summarizer is None:
            return Context(content, tuple(selected), omitted_items=len(omitted))

        available = self.budget.max_characters - len(content) - (1 if content else 0)
        if available < 1:
            return Context(content, tuple(selected), omitted_items=len(omitted))
        summary = self.summarizer.summarize(omitted, max_characters=available).strip()
        if len(summary) > available:
            summary = summary[:available]
        if summary:
            content = f"{content}\n{summary}" if content else summary
        return Context(
            content,
            tuple(selected),
            omitted_items=len(omitted),
            summarized=bool(summary),
            summary_sources=tuple(omitted) if summary else (),
        )

    def project(self, state: StateT, projector: StateProjector[StateT]) -> Context:
        """Build a context from an application's state projection."""

        return self.build(projector.project(state))

"""Provider-neutral retrieval contracts and context augmentation.

This module deliberately does not own document ingestion, chunking, embeddings, or
storage. Applications implement ``Retriever`` for their chosen retrieval system and
use ``RetrievalAugmenter`` to turn attributable evidence into bounded context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .context import Context, ContextBuilder, ContextItem
from .core import TraceSink
from .memory import MemoryKind, MemoryRepository
from .provenance import Provenance


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """A scored, attributable piece of evidence returned by a retriever."""

    content: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = field(
        default_factory=lambda: Provenance.external("retrieval")
    )


class Retriever(Protocol):
    """Application-provided evidence lookup boundary."""

    def retrieve(self, query: str, *, limit: int = 10) -> Sequence[RetrievalResult]: ...


class MemoryRetriever:
    """Adapt a ``MemoryRepository`` to the generic retriever contract."""

    def __init__(
        self, store: MemoryRepository, *, kind: MemoryKind | None = None
    ) -> None:
        self.store = store
        self.kind = kind

    def retrieve(self, query: str, *, limit: int = 10) -> Sequence[RetrievalResult]:
        return tuple(
            RetrievalResult(
                content=result.record.content,
                source=result.record.source,
                score=result.score,
                metadata={
                    **result.record.metadata,
                    "memory_kind": result.record.kind.value,
                    "confidence": result.record.confidence,
                },
                provenance=result.record.provenance,
            )
            for result in self.store.retrieve(query, limit=limit, kind=self.kind)
        )


class RetrievalAugmenter:
    """Converts retrieved evidence into the bounded context used by an application."""

    def __init__(
        self,
        retriever: Retriever,
        context_builder: ContextBuilder,
        *,
        trace: TraceSink | None = None,
    ) -> None:
        self.retriever = retriever
        self.context_builder = context_builder
        self.trace = trace

    def retrieve(self, query: str, *, limit: int = 10) -> tuple[RetrievalResult, ...]:
        """Retrieve evidence, normalizing ordering by descending relevance score."""

        results = tuple(self.retriever.retrieve(query, limit=limit))
        ranked = sorted(enumerate(results), key=lambda pair: (-pair[1].score, pair[0]))
        return tuple(result for _, result in ranked)

    def context_items(
        self,
        results: Sequence[RetrievalResult],
    ) -> tuple[ContextItem, ...]:
        """Expose retrieved evidence for apps that combine it with other context."""

        count = len(results)
        return tuple(
            ContextItem(
                content=result.content,
                source=result.source,
                priority=count - rank,
                metadata={
                    **result.metadata,
                    "retrieval_rank": rank,
                    "retrieval_score": result.score,
                },
                provenance=result.provenance,
            )
            for rank, result in enumerate(results, start=1)
        )

    def augment(self, query: str, *, limit: int = 10) -> Context:
        """Retrieve evidence and assemble it under the configured context budget."""

        results = self.retrieve(query, limit=limit)
        context = self.context_builder.build(self.context_items(results))
        if self.trace is not None:
            self.trace.record(
                "retrieval.completed",
                {
                    "result_count": len(results),
                    "context_item_count": len(context.items),
                    "omitted_item_count": context.omitted_items,
                    "summarized": context.summarized,
                },
            )
        return context

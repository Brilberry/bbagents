"""In-memory episodic, semantic, and procedural memory primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from uuid import uuid4

from .persistence import ConcurrentWriteError
from .provenance import Provenance


class MemoryKind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    content: str
    kind: MemoryKind = MemoryKind.EPISODIC
    confidence: float = 1.0
    source: str = "agent"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    embedding: Sequence[float] | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    version: int = 1
    provenance: Provenance = field(
        default_factory=lambda: Provenance.internal("memory")
    )

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.version < 1:
            raise ValueError("version must be positive")


@dataclass(frozen=True, slots=True)
class MemoryResult:
    record: MemoryRecord
    score: float


Embedder = Callable[[str], Sequence[float]]


class MemoryRepository(Protocol):
    """Application-owned durable memory repository with stable record identity."""

    def get(self, record_id: str) -> MemoryRecord | None: ...

    def remember(
        self,
        record: MemoryRecord,
        *,
        expected_version: int | None = None,
    ) -> MemoryRecord: ...

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        kind: MemoryKind | None = None,
    ) -> list[MemoryResult]: ...

    def forget(self, predicate: Callable[[MemoryRecord], bool]) -> int: ...


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions must match")
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(
        sum(x * x for x in right)
    )
    if not denominator:
        return 0.0
    return sum(x * y for x, y in zip(left, right, strict=True)) / denominator


class MemoryStore:
    """Versioned in-memory reference repository; replace it in production."""

    def __init__(self, embed: Embedder | None = None) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._embed = embed

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def remember(
        self,
        record: MemoryRecord,
        *,
        expected_version: int | None = None,
    ) -> MemoryRecord:
        existing = self._records.get(record.id)
        actual_version = existing.version if existing is not None else 0
        if expected_version is not None and expected_version != actual_version:
            raise ConcurrentWriteError(
                f"Memory record {record.id!r} is at version {actual_version}, "
                f"not {expected_version}"
            )
        embedding = record.embedding
        if record.embedding is None and self._embed is not None:
            embedding = tuple(self._embed(record.content))
        stored = replace(record, embedding=embedding, version=actual_version + 1)
        self._records[stored.id] = stored
        return stored

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        kind: MemoryKind | None = None,
    ) -> list[MemoryResult]:
        records = [
            record
            for record in self._records.values()
            if kind is None or record.kind == kind
        ]
        query_embedding = tuple(self._embed(query)) if self._embed is not None else None
        results = [
            MemoryResult(record, self._score(query, query_embedding, record))
            for record in records
        ]
        return sorted(results, key=lambda result: result.score, reverse=True)[:limit]

    def forget(self, predicate: Callable[[MemoryRecord], bool]) -> int:
        removed = [record.id for record in self._records.values() if predicate(record)]
        for record_id in removed:
            del self._records[record_id]
        return len(removed)

    def __iter__(self) -> Iterable[MemoryRecord]:
        return iter(tuple(self._records.values()))

    @staticmethod
    def _score(
        query: str,
        query_embedding: Sequence[float] | None,
        record: MemoryRecord,
    ) -> float:
        if query_embedding is not None and record.embedding is not None:
            relevance = cosine_similarity(query_embedding, record.embedding)
        else:
            terms = set(query.casefold().split())
            content_terms = set(record.content.casefold().split())
            relevance = len(terms & content_terms) / max(len(terms), 1)
        return relevance * record.confidence

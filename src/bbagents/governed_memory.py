"""Scoped memory with revision history and authorization before ranking."""

from dataclasses import asdict, replace
from datetime import datetime
from typing import Any, Callable

from .memory import Embedder, MemoryKind, MemoryRecord, MemoryResult, MemoryStore
from .provenance import Provenance, TrustLevel
from .revisions import RevisionRepository


class ScopedMemoryStore:
    def __init__(
        self,
        revisions: RevisionRepository[dict[str, Any]],
        *,
        embed: Embedder | None = None,
    ) -> None:
        self.revisions = revisions
        self.embed = embed

    def get(self, record_id: str) -> MemoryRecord | None:
        revision = self.revisions.current(record_id)
        return None if revision is None else _decode(revision.value, revision.number)

    def remember(
        self, record: MemoryRecord, *, expected_version: int | None = None
    ) -> MemoryRecord:
        history = self.revisions.history(record.id)
        revision = self.revisions.append(
            record.id,
            _encode(record),
            expected_revision=len(history)
            if expected_version is None
            else expected_version,
            reason="memory update",
            activate=True,
        )
        return replace(record, version=revision.number)

    def retrieve(
        self, query: str, *, limit: int = 10, kind: MemoryKind | None = None
    ) -> list[MemoryResult]:
        # Authorization and scope selection happen before embeddings or scoring.
        items = self.revisions.items()
        memory = MemoryStore(self.embed)
        versions: dict[str, int] = {}
        for item_id, revision in items:
            memory.remember(_decode(revision.value, revision.number))
            versions[item_id] = revision.number
        return [
            MemoryResult(
                replace(result.record, version=versions[result.record.id]), result.score
            )
            for result in memory.retrieve(query, limit=limit, kind=kind)
        ]

    def forget(self, predicate: Callable[[MemoryRecord], bool]) -> int:
        removed = 0
        for item_id, revision in self.revisions.items():
            if predicate(_decode(revision.value, revision.number)):
                self.revisions.activate(
                    item_id, None, expected_active=revision.number, reason="archived"
                )
                removed += 1
        return removed


def _encode(record: MemoryRecord) -> dict[str, Any]:
    data = asdict(record)
    data["created_at"] = record.created_at.isoformat()
    data["provenance"]["received_at"] = record.provenance.received_at.isoformat()
    return data


def _decode(data: dict[str, Any], version: int) -> MemoryRecord:
    values = dict(data)
    values["kind"] = MemoryKind(values["kind"])
    values["created_at"] = datetime.fromisoformat(values["created_at"])
    provenance = dict(values["provenance"])
    provenance["trust"] = TrustLevel(provenance["trust"])
    provenance["received_at"] = datetime.fromisoformat(provenance["received_at"])
    values["provenance"] = Provenance(**provenance)
    values["version"] = version
    return MemoryRecord(**values)

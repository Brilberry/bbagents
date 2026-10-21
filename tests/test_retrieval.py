from bbagents.context import ContextBudget, ContextBuilder
from bbagents.memory import MemoryKind, MemoryRecord, MemoryStore
from bbagents.observability import InMemoryTrace
from bbagents.provenance import Provenance, TrustLevel
from bbagents.retrieval import MemoryRetriever, RetrievalAugmenter, RetrievalResult


class StaticRetriever:
    def retrieve(self, query: str, *, limit: int = 10) -> list[RetrievalResult]:
        del query
        return [
            RetrievalResult("low relevance", "docs.low", 0.2),
            RetrievalResult("highest relevance", "docs.high", 0.9),
            RetrievalResult("middle relevance", "docs.middle", 0.5),
        ][:limit]


def test_retrieval_augmentation_ranks_evidence_and_preserves_provenance() -> None:
    trace = InMemoryTrace()
    augmenter = RetrievalAugmenter(
        StaticRetriever(),
        ContextBuilder(ContextBudget(max_items=2, max_characters=100)),
        trace=trace,
    )

    context = augmenter.augment("what should I do?", limit=3)

    assert context.content == "highest relevance\nmiddle relevance"
    assert [item.source for item in context.items] == ["docs.high", "docs.middle"]
    assert context.items[0].metadata == {
        "retrieval_rank": 1,
        "retrieval_score": 0.9,
    }
    assert context.omitted_items == 1
    assert trace.events_named("retrieval.completed")[0].payload["result_count"] == 3


def test_memory_retriever_adapts_existing_memory_records() -> None:
    memory = MemoryStore()
    memory.remember(
        MemoryRecord(
            "OAuth requires PKCE.",
            kind=MemoryKind.SEMANTIC,
            confidence=0.9,
            source="architecture.docs",
            metadata={"document_id": "auth-17"},
        )
    )

    result = MemoryRetriever(memory, kind=MemoryKind.SEMANTIC).retrieve("OAuth PKCE")[0]

    assert result.content == "OAuth requires PKCE."
    assert result.source == "architecture.docs"
    assert result.metadata == {
        "document_id": "auth-17",
        "memory_kind": "semantic",
        "confidence": 0.9,
    }


def test_retrieval_provenance_is_preserved_in_context() -> None:
    provenance = Provenance.external("advisory-feed")
    augmenter = RetrievalAugmenter(
        StaticRetrieverWithProvenance(provenance),
        ContextBuilder(ContextBudget(max_items=1, max_characters=100)),
    )

    context = augmenter.augment("advisory")

    assert context.items[0].provenance == provenance
    assert context.items[0].provenance.trust == TrustLevel.EXTERNAL


class StaticRetrieverWithProvenance:
    def __init__(self, provenance: Provenance) -> None:
        self.provenance = provenance

    def retrieve(self, query: str, *, limit: int = 10) -> list[RetrievalResult]:
        del query, limit
        return [RetrievalResult("advisory", "feed", 1.0, provenance=self.provenance)]

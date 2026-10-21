from bbagents.learning import Experience, LearningRuntime, Procedure
from bbagents.memory import MemoryKind, MemoryRecord, MemoryStore
from bbagents.persistence import ConcurrentWriteError
import pytest


def test_memory_retrieval_and_forgetting() -> None:
    memory = MemoryStore()
    memory.remember(
        MemoryRecord("OAuth uses PKCE", MemoryKind.SEMANTIC, confidence=0.9)
    )
    memory.remember(MemoryRecord("unrelated note", confidence=0.1))

    results = memory.retrieve("OAuth PKCE")

    assert results[0].record.content == "OAuth uses PKCE"
    assert memory.forget(lambda record: record.confidence < 0.5) == 1


def test_learning_promotes_only_validated_generalizations() -> None:
    runtime: LearningRuntime[str] = LearningRuntime(
        lambda experiences: Procedure("test-first", "run tests", confidence=0.8),
        lambda procedure: procedure.confidence >= 0.7,
    )
    runtime.observe(Experience("one", "passed", True))
    assert runtime.learn() is None
    runtime.observe(Experience("two", "passed", True))

    procedure = runtime.learn()

    assert procedure is not None
    assert procedure.name == "test-first"
    assert len(procedure.supporting_experiences) == 2


def test_learning_does_not_duplicate_evidence_when_relearning_same_history() -> None:
    procedure = Procedure("test-first", "run tests", confidence=0.8)
    runtime: LearningRuntime[str] = LearningRuntime(
        lambda experiences: procedure,
        lambda candidate: candidate.confidence >= 0.7,
    )
    runtime.observe(Experience("one", "passed", True))
    runtime.observe(Experience("two", "passed", True))

    first = runtime.learn()
    second = runtime.learn()

    assert first is procedure
    assert second is procedure
    assert procedure.supporting_experiences == [
        Experience("one", "passed", True),
        Experience("two", "passed", True),
    ]


def test_memory_records_have_stable_identity_and_optimistic_concurrency() -> None:
    memory = MemoryStore()
    first = memory.remember(MemoryRecord("OAuth uses PKCE"))
    second = memory.remember(
        MemoryRecord("OAuth requires PKCE", id=first.id),
        expected_version=first.version,
    )

    assert second.version == 2
    assert memory.get(first.id) == second
    with pytest.raises(ConcurrentWriteError):
        memory.remember(MemoryRecord("stale", id=first.id), expected_version=1)

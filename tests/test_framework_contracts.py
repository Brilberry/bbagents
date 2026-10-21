from pathlib import Path
from typing import Sequence

import pytest

from bbagents.context import ContextBudget, ContextBuilder, ContextItem
from bbagents.observability import (
    InMemoryTrace,
    KeyRedactor,
    TraceEvent,
    TraceEventName,
)
from bbagents.persistence import (
    ConcurrentWriteError,
    FileStateStore,
    InMemoryStateStore,
)
from bbagents.runtime import AuthorizationDecision
from bbagents.provenance import Provenance
from bbagents.safety import PolicyDenied, enforce


class JoinSummarizer:
    def summarize(self, items: Sequence[ContextItem], *, max_characters: int) -> str:
        del max_characters
        return "summary: " + ", ".join(item.source for item in items)


def test_context_builder_prioritizes_and_summarizes_omitted_items() -> None:
    context = ContextBuilder(
        ContextBudget(max_items=1, max_characters=31),
        summarizer=JoinSummarizer(),
    ).build(
        [
            ContextItem("low", "memory", priority=1),
            ContextItem("critical", "policy", priority=10),
            ContextItem("medium", "state", priority=5),
        ]
    )

    assert [(item.content, item.source, item.priority) for item in context.items] == [
        ("critical", "policy", 10)
    ]
    assert context.content == "critical\nsummary: state, memory"
    assert context.omitted_items == 2
    assert context.summarized
    assert [item.source for item in context.summary_sources] == ["state", "memory"]


def test_compaction_preserves_external_source_lineage_outside_model_text() -> None:
    source = ContextItem(
        "external document",
        "document:version-2",
        provenance=Provenance.external("tenant-a:document:version-2"),
    )
    context = ContextBuilder(
        ContextBudget(max_items=1, max_characters=60), summarizer=JoinSummarizer()
    ).build([ContextItem("policy", "policy", priority=10), source])
    assert context.summary_sources == (source,)
    assert context.summary_sources[0].provenance is source.provenance
    assert "external document" not in context.content


def test_state_store_uses_optimistic_concurrency() -> None:
    store: InMemoryStateStore[dict[str, str]] = InMemoryStateStore()
    first = store.save("release-17", {"status": "draft"})
    second = store.save(
        "release-17",
        {"status": "approved"},
        expected_version=first.version,
        metadata={"actor": "release-manager"},
    )

    assert second.version == 2
    assert store.load("release-17") == second
    with pytest.raises(ConcurrentWriteError):
        store.save("release-17", {"status": "stale"}, expected_version=1)


def test_file_state_store_treats_corrupt_and_incompatible_files_as_absent(
    tmp_path: Path,
) -> None:
    store: FileStateStore[dict[str, str]] = FileStateStore(tmp_path)
    snapshot = store.save("release-17", {"status": "draft"})
    path = store._path_for("release-17")

    path.write_text("{partial", encoding="utf-8")
    assert store.load("release-17") is None

    path.write_text(
        '{"format_version": 999, "key": "release-17"}',
        encoding="utf-8",
    )
    assert store.load("release-17") is None

    assert snapshot.version == 1
    recovered = store.save("release-17", {"status": "recovered"})
    assert recovered.version == 1
    assert store.load("release-17") == recovered


def test_file_state_store_uses_safe_filenames_for_application_keys(
    tmp_path: Path,
) -> None:
    key = "conversations/2026-08-24 12:00"
    store: FileStateStore[str] = FileStateStore(tmp_path)

    snapshot = store.save(key, "saved")
    path = store._path_for(key)

    assert path.parent == tmp_path
    assert path.suffix == ".json"
    assert store.load(key) == snapshot


def test_policy_contract_exposes_decision_before_enforcement() -> None:
    class RegionPolicy:
        def decide(self, action: str, region: str) -> AuthorizationDecision:
            if region == "protected":
                return AuthorizationDecision.deny("protected region")
            return AuthorizationDecision.allow("region permits action")

    policy = RegionPolicy()
    assert enforce(policy, "close_border", "coastal").allowed
    with pytest.raises(PolicyDenied, match="protected region"):
        enforce(policy, "close_border", "protected")


def test_child_trace_preserves_run_identity_and_links_parent_span() -> None:
    parent = InMemoryTrace()
    child = parent.child()
    child.record("agent.decision", {"action": "investigate"})

    event = child.events[0]
    assert event.run_id == parent.context.run_id
    assert event.parent_span_id == parent.context.span_id
    assert event.span_id == child.context.span_id


def test_trace_envelopes_support_spans_and_redaction() -> None:
    trace = InMemoryTrace(redactor=KeyRedactor())
    trace.record(
        TraceEvent(
            TraceEventName.DECISION_STARTED,
            {"token": "secret", "task": "go"},
        )
    )
    with trace.span("release", {"authorization": "Bearer secret"}) as context:
        assert context.run_id == trace.context.run_id

    decision = trace.events_named(TraceEventName.DECISION_STARTED)[0]
    assert decision.payload["token"] == "[REDACTED]"
    span_events = trace.events_named(TraceEventName.SPAN_STARTED)
    assert span_events[0].payload["authorization"] == "[REDACTED]"
    assert span_events[0].parent_span_id == trace.context.span_id

"""Trace consumers must support both string and enum event names."""

import json

import pytest

from bbagents.observability import InMemoryTrace, TraceEvent, TraceEventName


@pytest.mark.parametrize("name", [TraceEventName.TREE_OUTCOME, "behavior_tree.outcome"])
@pytest.mark.parametrize("envelope", [False, True])
def test_event_names_compare_and_serialize_without_enum_only_access(name, envelope):
    trace = InMemoryTrace()
    attributes = {"status": "success"}
    if envelope:
        trace.record(TraceEvent(name, attributes))
    else:
        trace.record(name, attributes)
        assert type(trace.events[0].name) is str

    event = trace.events[0]
    assert event.name == "behavior_tree.outcome"
    assert trace.events_named("behavior_tree.outcome") == [event]
    assert trace.events_named(TraceEventName.TREE_OUTCOME) == [event]
    assert json.loads(json.dumps({"name": event.name, "attributes": event.attributes})) == {
        "name": "behavior_tree.outcome", "attributes": attributes,
    }

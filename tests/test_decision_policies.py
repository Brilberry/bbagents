"""Player-facing AST validation and native compilation; no game dependencies."""

import json
import math

import pytest

from bbagents.behavior_trees import BehaviorTreeError, Execution, Status
from bbagents.decision_policies import DecisionCompiler
from bbagents.observability import InMemoryTrace
from bbagents.policy_registry import (
    ActionSpec,
    AttributeSpec,
    EnergyCost,
    Event,
    EventSpec,
    Frame,
    ParameterSpec,
    PolicyRegistry,
    UNAVAILABLE,
    ValueSpec,
)


class Work:
    def __init__(self, name, *, duration=1, result=Status.SUCCESS):
        self.name, self.duration, self.result = name, duration, result
        self.calls = []
        self.cancelled = []

    def tick(self, frame, parameters, memory):
        self.calls.append((frame.event.name, dict(parameters)))
        frame.context.setdefault("effects", []).append(self.name)
        memory["progress"] = memory.get("progress", 0) + 1
        return Status.RUNNING if memory["progress"] < self.duration else self.result

    def cancel(self, frame, parameters, memory):
        self.cancelled.append((frame.event.name, dict(memory)))


def registry(*, actions=None, trace=None, version="1"):
    handlers = actions or {
        name: Work(name) for name in ("attack", "flee", "eat", "rest", "travel")
    }
    number = ValueSpec("number", 0, 100)
    events = [
        EventSpec("idle", "Idle", "level", active=lambda frame: True),
        EventSpec(
            "danger",
            "Danger nearby",
            "level",
            active=lambda frame: frame.context.get("danger", False),
        ),
        EventSpec(
            "hungry",
            "Hungry",
            "level",
            fields=(
                ParameterSpec("start", number, False, 70),
                ParameterSpec("stop", number, False, 30),
            ),
            active=lambda frame: (
                frame.context.get("hunger", 0) >= frame.event.payload["start"]
            ),
            release=lambda frame: (
                frame.context.get("hunger", 0) <= frame.event.payload["stop"]
            ),
        ),
        EventSpec(
            "spotted",
            "Resource discovered",
            "occurrence",
            fields=(
                ParameterSpec("entity", ValueSpec("string")),
                ParameterSpec("distance", number),
            ),
        ),
    ]
    attributes = [
        AttributeSpec(
            "health",
            number,
            lambda frame: frame.context.get("health", 50),
            label="Health",
        ),
        AttributeSpec(
            "armed",
            ValueSpec("boolean"),
            lambda frame: frame.context.get("armed", False),
        ),
        AttributeSpec(
            "species",
            ValueSpec("string"),
            lambda frame: frame.context.get("species", "wolf"),
        ),
        AttributeSpec("unseen.health", number, lambda frame: UNAVAILABLE),
        AttributeSpec(
            "event.distance",
            number,
            lambda frame: frame.event.payload["distance"],
            events=("spotted",),
        ),
    ]
    specs = [
        ActionSpec(
            name,
            name.title(),
            handler,
            parameters=(ParameterSpec("amount", ValueSpec("number", 1, 10), False, 1),),
            energy=EnergyCost(4, "per_interval"),
            precondition=lambda frame, parameters: frame.context.get("allowed", True),
        )
        for name, handler in handlers.items()
    ]
    return PolicyRegistry(
        version=version, events=events, attributes=attributes, actions=specs
    ), handlers


def action(identity="act", operation="travel", **extra):
    return {"id": identity, "type": "action", "action": operation, **extra}


def compare(attribute="health", op=">", value=50):
    return {"type": "compare", "attribute": attribute, "op": op, "value": value}


def decision(expression=None, yes=None, no=None):
    return {
        "id": "decision",
        "type": "condition",
        "condition": expression or compare(),
        "yes": yes or action("attack", "attack"),
        "no": no or action("flee", "flee"),
    }


def policy(identity="test", event="idle", root=None, **extra):
    return {
        "version": 1,
        "id": identity,
        "event": event,
        "root": root or action(),
        **extra,
    }


def test_registry_catalog_is_serializable_and_detached_and_extensible():
    catalog, handlers = registry()
    data = catalog.catalog()
    assert json.loads(json.dumps(data)) == data
    assert data["actions"][0]["energy"] == {"amount": 4, "mode": "per_interval"}
    assert data["attributes"][0]["value"]["kind"] == "number"
    data["events"][0]["name"] = "changed"
    assert "idle" in catalog.events
    with pytest.raises(TypeError):
        catalog.actions["new"] = catalog.actions["travel"]
    assert not any(handler.calls for handler in handlers.values())


def test_compiler_uses_native_branch_and_does_not_execute_during_compilation():
    catalog, handlers = registry()
    compiled = DecisionCompiler(catalog).compile(policy(root=decision()))
    assert compiled.tree.to_data()["root"]["type"] == "branch"
    assert not any(handler.calls for handler in handlers.values())
    assert (
        compiled.tree.start().tick(Frame({"health": 70}, Event("idle")))
        == Status.SUCCESS
    )
    assert handlers["attack"].calls and not handlers["flee"].calls


@pytest.mark.parametrize(
    "op,value,expected",
    [
        (">", 50, False),
        (">=", 50, True),
        ("<", 51, True),
        ("<=", 49, False),
        ("==", 50, True),
        ("!=", 50, False),
    ],
)
def test_all_comparison_operators(op, value, expected):
    catalog, handlers = registry()
    compiled = DecisionCompiler(catalog).compile(
        policy(root=decision(compare(op=op, value=value)))
    )
    compiled.tree.start().tick(Frame({"health": 50}, Event("idle")))
    assert bool(handlers["attack"].calls) is expected
    assert bool(handlers["flee"].calls) is not expected


def test_boolean_combinations_and_string_comparison():
    catalog, handlers = registry()
    expression = {
        "type": "and",
        "children": [
            compare("armed", "==", True),
            {
                "type": "or",
                "children": [
                    compare("health", ">", 70),
                    compare("species", "==", "bear"),
                ],
            },
            {"type": "not", "child": compare("health", "<", 20)},
        ],
    }
    compiled = DecisionCompiler(catalog).compile(policy(root=decision(expression)))
    compiled.tree.start().tick(
        Frame({"health": 50, "armed": True, "species": "bear"}, Event("idle"))
    )
    assert handlers["attack"].calls


@pytest.mark.parametrize(
    "expression",
    [
        compare("unseen.health", "!=", 50),
        {"type": "not", "child": compare("unseen.health", ">", 50)},
        {
            "type": "and",
            "children": [compare("health", ">", 10), compare("unseen.health", ">", 50)],
        },
        {
            "type": "or",
            "children": [compare("health", "<", 10), compare("unseen.health", ">", 50)],
        },
    ],
)
def test_unknown_observations_cannot_be_inverted_into_true(expression):
    catalog, handlers = registry()
    compiled = DecisionCompiler(catalog).compile(policy(root=decision(expression)))
    compiled.tree.start().tick(Frame({}, Event("idle")))
    assert not handlers["attack"].calls and handlers["flee"].calls


def test_known_true_or_unknown_is_true_and_known_false_and_unknown_is_false():
    catalog, handlers = registry()
    expression = {
        "type": "or",
        "children": [compare("unseen.health", ">", 50), compare("health", ">", 10)],
    }
    compiled = DecisionCompiler(catalog).compile(policy(root=decision(expression)))
    compiled.tree.start().tick(Frame({}, Event("idle")))
    assert handlers["attack"].calls


def test_contextual_attributes_require_matching_event():
    catalog, handlers = registry()
    data = policy(event="spotted", root=decision(compare("event.distance", "<", 10)))
    compiled = DecisionCompiler(catalog).compile(data)
    event = catalog.event(
        Event("spotted", 1, {"entity": "berry-bush", "distance": 2}), occurrence=True
    )
    compiled.tree.start().tick(Frame({}, event))
    assert handlers["attack"].calls
    data["event"] = "idle"
    with pytest.raises(ValueError, match="context"):
        DecisionCompiler(catalog).compile(data)


def test_action_failure_does_not_execute_opposite_decision_arm():
    catalog, handlers = registry(
        actions={"attack": Work("attack", result=Status.FAILURE), "flee": Work("flee")}
    )
    compiled = DecisionCompiler(catalog).compile(policy(root=decision()))
    assert (
        compiled.tree.start().tick(Frame({"health": 80}, Event("idle")))
        == Status.FAILURE
    )
    assert not handlers["flee"].calls


def test_explicit_failure_outcome_runs_and_survives_checkpoint_without_retry():
    trace = InMemoryTrace()
    catalog, handlers = registry(
        actions={
            "attack": Work("attack", result=Status.FAILURE),
            "flee": Work("flee", duration=3),
            "eat": Work("eat"),
        }
    )
    root = decision(
        yes=action("attack", "attack", failure=action("recover", "flee")),
        no=action("other", "eat"),
    )
    compiler = DecisionCompiler(catalog, trace=trace)
    compiled = compiler.compile(policy(root=root))
    run = compiled.tree.start()
    assert run.tick(Frame({"health": 80}, Event("idle"))) == Status.RUNNING
    state = json.loads(json.dumps(run.snapshot()))
    assert state["outcomes"] == {"attack": "failure"}
    run = Execution.restore(compiler.compile(compiled.to_data()).tree, state)
    assert run.tick(Frame({"health": 10}, Event("idle"))) == Status.RUNNING
    assert run.tick(Frame({"health": 10}, Event("idle"))) == Status.SUCCESS
    assert len(handlers["attack"].calls) == 1
    assert not handlers["eat"].calls
    assert any(event.name == "behavior_tree.outcome" for event in trace.events)


def test_explicit_success_outcome_failure_is_returned_without_retry():
    catalog, handlers = registry(
        actions={"attack": Work("attack"), "flee": Work("flee", result=Status.FAILURE)}
    )
    compiled = DecisionCompiler(catalog).compile(
        policy(root=action("first", "attack", success=action("second", "flee")))
    )
    assert compiled.tree.start().tick(Frame({}, Event("idle"))) == Status.FAILURE
    assert len(handlers["attack"].calls) == len(handlers["flee"].calls) == 1


def test_parameters_defaults_detachment_and_semantic_fingerprints():
    catalog, handlers = registry()
    compiler = DecisionCompiler(catalog)
    data = policy(root=action(parameters={"amount": 3}))
    compiled = compiler.compile(data)
    equivalent = compiler.compile(compiled.to_data())
    assert compiled.fingerprint == equivalent.fingerprint
    assert compiled.tree.fingerprint == equivalent.tree.fingerprint
    data["root"]["parameters"]["amount"] = 7
    compiled.tree.start().tick(Frame({}, Event("idle")))
    assert handlers["travel"].calls[-1][1] == {"amount": 3}
    assert compiler.compile(data).tree.fingerprint != compiled.tree.fingerprint
    default = compiler.compile(policy())
    assert (
        default.tree.fingerprint == compiler.compile(default.to_data()).tree.fingerprint
    )


def test_revalidating_preconditions_cancels_running_work_and_does_not_fallback():
    catalog, handlers = registry(
        actions={"attack": Work("attack", duration=3), "flee": Work("flee")}
    )
    compiled = DecisionCompiler(catalog).compile(policy(root=decision()))
    run = compiled.tree.start()
    assert run.tick(Frame({"health": 80}, Event("idle"))) == Status.RUNNING
    assert (
        run.tick(Frame({"health": 80, "allowed": False}, Event("idle")))
        == Status.FAILURE
    )
    assert len(handlers["attack"].calls) == len(handlers["attack"].cancelled) == 1
    assert not handlers["flee"].calls


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(version=True),
        lambda d: d.update(priority=True),
        lambda d: d.update(cooldown_steps=-1),
        lambda d: d.update(enabled=1),
        lambda d: d.update(event="unknown"),
        lambda d: d.update(extra=0),
        lambda d: d["root"].update(action="unknown"),
        lambda d: d["root"].update(parameters={"unknown": 1}),
        lambda d: d["root"].update(parameters={"amount": True}),
        lambda d: d["root"].update(parameters={"amount": math.nan}),
        lambda d: d["root"].update(parameters={"amount": 100}),
        lambda d: d["root"].update(success=None),
    ],
)
def test_invalid_policy_and_action_data_rejected(change):
    catalog, _ = registry()
    data = policy()
    change(data)
    with pytest.raises((ValueError, TypeError)):
        DecisionCompiler(catalog).compile(data)


@pytest.mark.parametrize(
    "expression",
    [
        compare("unknown"),
        compare("health", "eval", 1),
        compare("armed", ">", True),
        compare("armed", "==", 1),
        compare("health", ">", "50"),
        {"type": "not", "child": compare(), "extra": 1},
        {"type": "and", "children": []},
        {"type": "or", "children": [None]},
    ],
)
def test_invalid_conditions_rejected(expression):
    catalog, _ = registry()
    with pytest.raises(ValueError):
        DecisionCompiler(catalog).compile(policy(root=decision(expression)))


def test_missing_branches_duplicate_ids_and_cyclic_data_rejected():
    catalog, _ = registry()
    compiler = DecisionCompiler(catalog)
    root = decision()
    del root["no"]
    with pytest.raises(ValueError, match="YES/NO"):
        compiler.compile(policy(root=root))
    root = decision(yes=action("same"), no=action("same"))
    with pytest.raises(ValueError, match="unique"):
        compiler.compile(policy(root=root))
    root["yes"] = root
    with pytest.raises(ValueError, match="limits"):
        compiler.compile(policy(root=root))


def test_runtime_reader_contract_failure_is_not_false_condition():
    catalog, handlers = registry()
    bad = AttributeSpec("bad", ValueSpec("boolean"), lambda frame: 1)
    catalog = PolicyRegistry(
        version="1",
        events=catalog.events.values(),
        attributes=[bad],
        actions=catalog.actions.values(),
    )
    compiled = DecisionCompiler(catalog).compile(
        policy(root=decision(compare("bad", "==", True)))
    )
    with pytest.raises(BehaviorTreeError):
        compiled.tree.start().tick(Frame({}, Event("idle")))
    assert not any(handler.calls for handler in handlers.values())


def test_registry_configuration_errors_are_early_and_descriptive():
    catalog, _ = registry()
    with pytest.raises(ValueError, match="Duplicate"):
        PolicyRegistry(
            version="1", events=[catalog.events["idle"]] * 2, attributes=[], actions=[]
        )
    with pytest.raises(ValueError, match="active"):
        PolicyRegistry(
            version="1",
            events=[EventSpec("bad", "Bad", "level")],
            attributes=[],
            actions=[],
        )
    with pytest.raises(ValueError):
        ValueSpec("boolean", minimum=0)
    with pytest.raises(ValueError):
        ParameterSpec("bad", ValueSpec("boolean"), False, 1)
    with pytest.raises(ValueError):
        EnergyCost(-1)
    with pytest.raises(ValueError, match="context"):
        PolicyRegistry(
            version="1",
            events=catalog.events.values(),
            attributes=[
                AttributeSpec(
                    "bad", ValueSpec("number"), lambda f: 0, events=("unknown",)
                )
            ],
            actions=[],
        )


def test_scheduler_priorities_ties_disabled_policies_and_registration_snapshot():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry()
    compiler = DecisionCompiler(catalog)
    rules = [
        compiler.compile(policy("low", root=action(operation="rest"), priority=-5)),
        compiler.compile(policy("first", root=action(operation="eat"), priority=10)),
        compiler.compile(
            policy("second", root=action(operation="attack"), priority=10)
        ),
        compiler.compile(
            policy(
                "disabled", root=action(operation="flee"), priority=100, enabled=False
            )
        ),
    ]
    scheduler = PolicyScheduler(catalog, rules)
    rules.clear()
    assert scheduler.tick({}, step=0).policy == "first"
    assert handlers["eat"].calls
    assert not handlers["attack"].calls and not handlers["flee"].calls


def test_scheduler_hysteresis_custom_event_thresholds_and_release_cancellation():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry(actions={"eat": Work("eat", duration=20)})
    compiled = DecisionCompiler(catalog).compile(
        policy(
            event="hungry",
            event_parameters={"start": 60, "stop": 20},
            root=action(operation="eat"),
        )
    )
    scheduler = PolicyScheduler(catalog, [compiled])
    assert scheduler.tick({"hunger": 59}, step=0).policy is None
    assert scheduler.tick({"hunger": 60}, step=1).status == Status.RUNNING
    assert scheduler.tick({"hunger": 40}, step=2).status == Status.RUNNING
    assert scheduler.tick({"hunger": 21}, step=3).status == Status.RUNNING
    assert scheduler.tick({"hunger": 20}, step=4).policy is None
    assert len(handlers["eat"].calls) == 3 and len(handlers["eat"].cancelled) == 1
    assert scheduler.tick({"hunger": 40}, step=5).policy is None


def test_event_latches_are_per_policy_not_shared_between_thresholds():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry()
    compiler = DecisionCompiler(catalog)
    low = compiler.compile(
        policy(
            "early", "hungry", event_parameters={"start": 40, "stop": 10}, priority=10
        )
    )
    high = compiler.compile(
        policy(
            "late", "hungry", event_parameters={"start": 80, "stop": 30}, priority=20
        )
    )
    scheduler = PolicyScheduler(catalog, [low, high])
    assert scheduler.tick({"hunger": 50}, step=0).policy == "early"
    assert scheduler.snapshot()["levels"] == {"early": True, "late": False}
    assert scheduler.tick({"hunger": 90}, step=1).policy == "late"


def test_higher_priority_cancels_before_effect_and_running_equal_priority_is_stable():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry(
        actions={"travel": Work("travel", duration=5), "flee": Work("flee", duration=2)}
    )
    compiler = DecisionCompiler(catalog)
    low = compiler.compile(policy("low", root=action(operation="travel")))
    high = compiler.compile(
        policy("danger", "danger", root=action(operation="flee"), priority=100)
    )
    scheduler = PolicyScheduler(catalog, [low, high])
    assert scheduler.tick({}, step=0).policy == "low"
    assert scheduler.tick({"danger": True}, step=1).policy == "danger"
    assert len(handlers["travel"].cancelled) == 1
    assert len(handlers["travel"].calls) == 1
    assert scheduler.tick({"danger": True}, step=2).status == Status.SUCCESS
    assert scheduler.tick({}, step=3).policy == "low"
    assert (
        scheduler.snapshot()["active"]["execution"]["memory"]["act"]["work"]["progress"]
        == 1
    )
    # Earlier registration may become eligible, but it cannot preempt equal-ranked running work.
    first = compiler.compile(policy("first", "danger", root=action(operation="flee")))
    second = compiler.compile(policy("second", root=action(operation="travel")))
    scheduler = PolicyScheduler(catalog, [first, second])
    assert scheduler.tick({}, step=0).policy == "second"
    assert scheduler.tick({"danger": True}, step=1).policy == "second"


def spotted(sequence, entity="berries"):
    return Event("spotted", sequence, {"entity": entity, "distance": 2})


def test_occurrences_queue_through_cooldown_and_replays_are_idempotent():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry()
    compiled = DecisionCompiler(catalog).compile(
        policy(event="spotted", cooldown_steps=2)
    )
    scheduler = PolicyScheduler(catalog, [compiled])
    assert (
        scheduler.tick({}, step=0, events=[spotted(1), spotted(2)]).event_sequence == 1
    )
    assert scheduler.tick({}, step=1, events=[spotted(1), spotted(2)]).policy is None
    assert scheduler.tick({}, step=2).policy is None
    assert scheduler.tick({}, step=3).event_sequence == 2
    assert scheduler.tick({}, step=6).policy is None
    assert len(handlers["travel"].calls) == 2


def test_preempted_occurrence_is_requeued_ahead_of_newer_work():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry(
        actions={"travel": Work("travel", duration=4), "flee": Work("flee")}
    )
    compiler = DecisionCompiler(catalog)
    gather = compiler.compile(policy("gather", "spotted"))
    danger = compiler.compile(
        policy("danger", "danger", root=action(operation="flee"), priority=100)
    )
    scheduler = PolicyScheduler(catalog, [gather, danger])
    assert scheduler.tick({}, step=0, events=[spotted(1)]).event_sequence == 1
    assert (
        scheduler.tick({"danger": True}, step=1, events=[spotted(2)]).policy == "danger"
    )
    assert [
        event["sequence"] for event in scheduler.snapshot()["pending"]["gather"]
    ] == [1, 2]
    assert scheduler.tick({}, step=2).event_sequence == 1
    assert len(handlers["travel"].cancelled) == 1


def test_occurrence_broadcast_is_per_policy_and_lower_priority_waits():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry()
    compiler = DecisionCompiler(catalog)
    high = compiler.compile(policy("high", "spotted", priority=100))
    low = compiler.compile(policy("low", "spotted", priority=10))
    scheduler = PolicyScheduler(catalog, [low, high])
    assert scheduler.tick({}, step=0, events=[spotted(1)]).policy == "high"
    assert scheduler.tick({}, step=1).policy == "low"
    assert scheduler.tick({}, step=2).policy is None


def test_queue_overflow_and_invalid_input_are_atomic_and_recoverable():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry(actions={"travel": Work("travel", duration=5)})
    compiled = DecisionCompiler(catalog).compile(policy(event="spotted"))
    scheduler = PolicyScheduler(catalog, [compiled], max_pending=1)
    scheduler.tick({}, step=0, events=[spotted(1)])
    before = scheduler.snapshot()
    with pytest.raises(ValueError, match="capacity"):
        scheduler.tick({}, step=1, events=[spotted(2)])
    assert scheduler.snapshot() == before
    with pytest.raises(ValueError, match="increasing"):
        scheduler.tick({}, step=1, events=[spotted(3), spotted(2)])
    assert scheduler.snapshot() == before
    with pytest.raises(ValueError):
        scheduler.tick({}, step=1, events=[Event("spotted", 2, {"entity": "unknown"})])
    assert scheduler.snapshot() == before
    assert scheduler.tick({}, step=1).status == Status.RUNNING


def test_cancellation_failure_blocks_higher_priority_until_cleanup_succeeds():
    from bbagents.policy_scheduler import PolicyScheduler

    class CannotStop(Work):
        def cancel(self, frame, parameters, memory):
            if not frame.context.get("cleanup", False):
                raise RuntimeError("secret error")
            super().cancel(frame, parameters, memory)

    catalog, handlers = registry(
        actions={"travel": CannotStop("travel", duration=5), "flee": Work("flee")}
    )
    compiler = DecisionCompiler(catalog)
    rules = [
        compiler.compile(policy("low")),
        compiler.compile(
            policy("high", "danger", root=action(operation="flee"), priority=100)
        ),
    ]
    scheduler = PolicyScheduler(catalog, rules)
    scheduler.tick({}, step=0)
    with pytest.raises(BehaviorTreeError):
        scheduler.tick({"danger": True}, step=1)
    assert not handlers["flee"].calls
    with pytest.raises(RuntimeError):
        scheduler.tick({"danger": True}, step=2)
    with pytest.raises(RuntimeError):
        scheduler.snapshot()
    scheduler.cancel({"cleanup": True})
    assert scheduler.tick({"danger": True}, step=2).policy == "high"


def test_scheduler_checkpoint_roundtrip_preserves_order_hysteresis_and_progress():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry(
        actions={"travel": Work("travel", duration=4), "eat": Work("eat", duration=4)}
    )
    compiler = DecisionCompiler(catalog)
    rules = [
        compiler.compile(policy("gather", "spotted")),
        compiler.compile(
            policy("food", "hungry", root=action(operation="eat"), priority=80)
        ),
    ]
    scheduler = PolicyScheduler(catalog, rules)
    scheduler.tick({}, step=0, events=[spotted(1), spotted(2)])
    scheduler.tick({"hunger": 80}, step=1)
    checkpoint = json.loads(json.dumps(scheduler.snapshot()))
    restored = PolicyScheduler.restore(
        catalog, [compiler.compile(rule.to_data()) for rule in rules], checkpoint
    )
    assert restored.snapshot() == scheduler.snapshot()
    for step, hunger in ((2, 50), (3, 20), (4, 20), (5, 20), (6, 20)):
        assert restored.tick({"hunger": hunger}, step=step) == scheduler.tick(
            {"hunger": hunger}, step=step
        )
        assert restored.snapshot() == scheduler.snapshot()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(version=True),
        lambda s: s.update(scheduler="wrong"),
        lambda s: s.update(last_step=True),
        lambda s: s.update(sequence=-2),
        lambda s: s["levels"].update(extra=True),
        lambda s: s["ready"].update(test=True),
        lambda s: s["active"].update(policy="missing"),
        lambda s: s["active"]["event"].update(name="idle"),
        lambda s: s["active"]["event"].update(sequence=999),
        lambda s: s["pending"]["test"].append(
            {
                "name": "spotted",
                "sequence": 1,
                "payload": {"entity": "berries", "distance": 2},
            }
        ),
    ],
)
def test_inconsistent_scheduler_checkpoint_rejected(change):
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry(actions={"travel": Work("travel", duration=4)})
    rules = [DecisionCompiler(catalog).compile(policy(event="spotted"))]
    scheduler = PolicyScheduler(catalog, rules)
    scheduler.tick({}, step=0, events=[spotted(1), spotted(2)])
    state = scheduler.snapshot()
    change(state)
    with pytest.raises(ValueError):
        PolicyScheduler.restore(catalog, rules, state)


def test_level_predicate_error_does_not_fall_back_or_expose_context():
    from bbagents.policy_scheduler import PolicyScheduler, PolicySchedulingError

    catalog, handlers = registry()

    def broken(frame):
        raise ValueError("private-value")

    catalog = PolicyRegistry(
        version="1",
        events=[EventSpec("bad", "Bad", "level", active=broken)],
        attributes=[],
        actions=catalog.actions.values(),
    )
    rules = [DecisionCompiler(catalog).compile(policy(event="bad"))]
    scheduler = PolicyScheduler(catalog, rules)
    with pytest.raises(PolicySchedulingError) as exc:
        scheduler.tick({}, step=0)
    assert "private-value" not in str(exc.value)
    assert isinstance(exc.value.__cause__, ValueError)
    assert not any(handler.calls for handler in handlers.values())
    with pytest.raises(RuntimeError):
        scheduler.tick({}, step=1)
    scheduler.cancel({})


def test_scheduler_failure_does_not_automatically_execute_other_policy():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, handlers = registry(
        actions={"attack": Work("attack", result=Status.FAILURE), "flee": Work("flee")}
    )
    compiler = DecisionCompiler(catalog)
    rules = [
        compiler.compile(
            policy(
                "high", root=action(operation="attack"), priority=100, cooldown_steps=1
            )
        ),
        compiler.compile(policy("low", root=action(operation="flee"))),
    ]
    scheduler = PolicyScheduler(catalog, rules)
    assert scheduler.tick({}, step=0).status == Status.FAILURE
    assert not handlers["flee"].calls
    assert scheduler.tick({}, step=1).policy == "low"


def test_scheduler_traces_do_not_expose_context_or_event_payload():
    from bbagents.policy_scheduler import PolicyScheduler

    trace = InMemoryTrace()
    catalog, _ = registry()
    rules = [DecisionCompiler(catalog, trace=trace).compile(policy(event="spotted"))]
    scheduler = PolicyScheduler(catalog, rules, trace=trace)
    scheduler.tick(
        {"secret": "private-value"}, step=0, events=[spotted(1, "private-entity")]
    )
    attributes = [dict(event.attributes) for event in trace.events]
    assert "private-value" not in repr(attributes) and "private-entity" not in repr(
        attributes
    )
    assert [event.name for event in trace.events] == [
        "decision_policy.selected",
        "behavior_tree.node_entered",
        "behavior_tree.node_finished",
        "behavior_tree.tick_finished",
        "decision_policy.finished",
    ]


def test_scheduler_rejects_wrong_registry_duplicate_policy_and_invalid_steps():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry()
    compiled = DecisionCompiler(catalog).compile(policy())
    with pytest.raises(ValueError, match="Duplicate"):
        PolicyScheduler(catalog, [compiled, compiled])
    other, _ = registry(version="2")
    with pytest.raises(ValueError, match="registry"):
        PolicyScheduler(other, [compiled])
    scheduler = PolicyScheduler(catalog, [compiled])
    scheduler.tick({}, step=0)
    for step in (0, -1, True, 0.5):
        with pytest.raises(ValueError):
            scheduler.tick({}, step=step)


def test_offline_scheduler_example_replays_identically_across_hash_seeds():
    import os
    from pathlib import Path
    import subprocess
    import sys
    from examples.decision_tree_policies import run_example

    result = run_example()
    assert result["selected"] == ["food", "danger", "food", None]
    assert result["evidence"] == ["food interval", "escape interval", "food interval"]
    root = Path(__file__).resolve().parents[1]
    outputs = []
    for seed in ("1", "42"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root / "src"))
        completed = subprocess.run(
            [sys.executable, str(root / "examples/decision_tree_policies.py")],
            env=env,
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        outputs.append(json.loads(completed.stdout))
    assert outputs[0] == outputs[1] == result


def test_scheduler_reentrancy_during_event_intake_is_rejected():
    from bbagents.policy_scheduler import PolicyScheduler

    catalog, _ = registry()
    scheduler = PolicyScheduler(
        catalog, [DecisionCompiler(catalog).compile(policy(event="spotted"))]
    )

    def events():
        scheduler.tick({}, step=1)
        yield spotted(1)

    with pytest.raises(RuntimeError, match="busy"):
        scheduler.tick({}, step=0, events=events())
    assert scheduler.snapshot()["last_step"] == -1
    assert scheduler.tick({}, step=0, events=[spotted(1)]).status == Status.SUCCESS


def test_scheduler_trace_failure_does_not_silently_repeat_completed_effect():
    from bbagents.policy_scheduler import PolicyScheduler

    class BrokenTrace:
        def record(self, event, payload):
            if event.value == "decision_policy.finished":
                raise OSError("trace offline")

    catalog, handlers = registry()
    scheduler = PolicyScheduler(
        catalog, [DecisionCompiler(catalog).compile(policy())], trace=BrokenTrace()
    )
    with pytest.raises(OSError):
        scheduler.tick({}, step=0)
    with pytest.raises(RuntimeError):
        scheduler.tick({}, step=1)
    assert len(handlers["travel"].calls) == 1
    scheduler.cancel({})
    with pytest.raises(ValueError):
        scheduler.tick({}, step=0)

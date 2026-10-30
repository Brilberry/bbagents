"""Native execution semantics, actor isolation, and safe decision branches."""

import copy
import json

import pytest

from bbagents.behavior_trees import (
    Action,
    BehaviorTree,
    BehaviorTreeError,
    Branch,
    Condition,
    Execution,
    Inverter,
    Selector,
    Sequence,
    Status,
)
from bbagents.observability import InMemoryTrace


class Work:
    def __init__(self, duration=1, result=Status.SUCCESS):
        self.duration = duration
        self.result = result
        self.calls = []
        self.cancellations = []

    def tick(self, context, memory):
        self.calls.append(context)
        memory["progress"] = memory.get("progress", 0) + 1
        return Status.RUNNING if memory["progress"] < self.duration else self.result

    def cancel(self, context, memory):
        self.cancellations.append((context, dict(memory)))


def test_sequence_resumes_without_reexecuting_completed_effects():
    first, long, last = Work(), Work(3), Work()
    tree = BehaviorTree(
        Sequence(
            "root",
            (Action("first", "first"), Action("long", "long"), Action("last", "last")),
        ),
        actions={"first": first, "long": long, "last": last},
        conditions={},
    )
    actor = tree.start()
    assert actor.tick("A") == Status.RUNNING
    assert actor.tick("A") == Status.RUNNING
    assert len(first.calls) == 1 and not last.calls
    assert actor.tick("A") == Status.SUCCESS
    assert len(first.calls) == 1 and len(last.calls) == 1
    assert actor.tick("A") == Status.RUNNING  # terminal traversal restarts
    assert len(first.calls) == 2


def test_selector_fallback_and_memory_and_inverter():
    fails, long, uncalled = Work(result=Status.FAILURE), Work(2), Work()
    tree = BehaviorTree(
        Selector(
            "root",
            (
                Action("fails", "fails"),
                Inverter("invert", Action("long", "long")),
                Action("last", "uncalled"),
            ),
        ),
        actions={"fails": fails, "long": long, "uncalled": uncalled},
        conditions={},
    )
    actor = tree.start()
    assert actor.tick(None) == Status.RUNNING
    assert actor.tick(None) == Status.SUCCESS
    assert len(fails.calls) == 1 and len(uncalled.calls) == 1


@pytest.mark.parametrize("yes", [True, False])
def test_branch_failure_never_executes_other_arm(yes):
    attack, flee = Work(result=Status.FAILURE), Work(result=Status.FAILURE)
    tree = BehaviorTree(
        Branch("decision", "armed", Action("attack", "attack"), Action("flee", "flee")),
        actions={"attack": attack, "flee": flee},
        conditions={"armed": lambda c: c},
    )
    assert tree.start().tick(yes) == Status.FAILURE
    assert bool(attack.calls) is yes
    assert bool(flee.calls) is not yes


def test_running_branch_is_pinned_until_cancel_then_reconsidered():
    attack, flee = Work(3), Work()
    tree = BehaviorTree(
        Branch("decision", "armed", Action("attack", "attack"), Action("flee", "flee")),
        actions={"attack": attack, "flee": flee},
        conditions={"armed": lambda c: c},
    )
    actor = tree.start()
    assert actor.tick(True) == Status.RUNNING
    assert actor.tick(False) == Status.RUNNING
    assert not flee.calls
    actor.cancel(False)
    assert attack.cancellations == [(False, {"progress": 2})]
    assert actor.tick(False) == Status.SUCCESS
    actor.cancel(False)  # no duplicate cancellation of terminal actions
    assert len(attack.cancellations) == 1


def test_isolated_actors_and_json_checkpoint_roundtrip():
    work = Work(3)
    tree = BehaviorTree(
        Branch(
            "branch",
            "ok",
            Sequence(
                "seq",
                (Condition("check", "ok"), Inverter("inv", Action("work", "work"))),
            ),
            Action("other", "work"),
        ),
        actions={"work": work},
        conditions={"ok": lambda c: c},
    )
    first, second = tree.start(), tree.start()
    assert first.tick(True) == Status.RUNNING
    assert first.tick(True) == Status.RUNNING
    assert second.tick(True) == Status.RUNNING
    serialized = json.loads(json.dumps(first.snapshot()))
    restored_tree = BehaviorTree.from_data(
        json.loads(json.dumps(tree.to_data())),
        actions={"work": work},
        conditions={"ok": lambda c: c},
    )
    restored = Execution.restore(restored_tree, serialized)
    assert restored.tick(True) == Status.FAILURE
    assert first.tick(True) == Status.FAILURE
    assert second.tick(True) == Status.RUNNING
    assert restored.snapshot() == first.snapshot()
    assert Execution.restore(tree, first.snapshot()).snapshot() == first.snapshot()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(version=True),
        lambda s: s.update(tree="wrong"),
        lambda s: s.update(step=-1),
        lambda s: s.update(active="missing"),
        lambda s: s["cursors"].update(root=True),
        lambda s: s["cursors"].update(root=10),
        lambda s: s["memory"].update(extra={}),
        lambda s: s["branches"].update(root=True),
        lambda s: s.update(active=None),
    ],
)
def test_reject_inconsistent_checkpoint(change):
    tree = BehaviorTree(
        Sequence("root", (Action("work", "work"),)),
        actions={"work": Work(3)},
        conditions={},
    )
    actor = tree.start()
    actor.tick(None)
    state = actor.snapshot()
    change(state)
    with pytest.raises(ValueError):
        Execution.restore(tree, state)


def test_checkpoint_is_detached_and_json_only():
    tree = BehaviorTree(
        Action("work", "work"), actions={"work": Work(4)}, conditions={}
    )
    actor = tree.start()
    actor.tick(None)
    state = actor.snapshot()
    state["memory"]["work"]["progress"] = 90
    assert actor.snapshot()["memory"]["work"]["progress"] == 1
    actor._memory["work"]["bad"] = {1: "integer key"}
    with pytest.raises(ValueError, match="JSON"):
        actor.snapshot()


def test_callback_errors_fail_closed_do_not_run_fallback_and_can_cancel():
    class Broken(Work):
        def tick(self, context, memory):
            memory["started"] = True
            raise ValueError("secret context")

    broken, fallback = Broken(), Work()
    actor = BehaviorTree(
        Selector("root", (Action("bad", "bad"), Action("fallback", "fallback"))),
        actions={"bad": broken, "fallback": fallback},
        conditions={},
    ).start()
    with pytest.raises(BehaviorTreeError, match="ValueError") as exc:
        actor.tick(None)
    assert "secret" not in str(exc.value)
    assert isinstance(exc.value.__cause__, ValueError)
    assert not fallback.calls
    with pytest.raises(RuntimeError):
        actor.tick(None)
    with pytest.raises(RuntimeError):
        actor.snapshot()
    actor.cancel(None)
    assert broken.cancellations == [(None, {"started": True})]
    assert actor.snapshot()["active"] is None


def test_cancel_failure_preserves_cleanup_state_and_blocks_reexecution():
    class BrokenCancel(Work):
        def cancel(self, context, memory):
            if not context:
                raise RuntimeError("could not stop")
            super().cancel(context, memory)

    work = BrokenCancel(3)
    actor = BehaviorTree(
        Action("work", "work"), actions={"work": work}, conditions={}
    ).start()
    actor.tick(None)
    with pytest.raises(BehaviorTreeError):
        actor.cancel(False)
    with pytest.raises(RuntimeError):
        actor.tick(None)
    actor.cancel(True)
    assert work.cancellations == [(True, {"progress": 1})]
    assert actor.tick(None) == Status.RUNNING


def test_contract_errors_and_baseexception_propagation():
    class Invalid(Work):
        def tick(self, context, memory):
            return "success"

    actor = BehaviorTree(
        Action("bad", "bad"), actions={"bad": Invalid()}, conditions={}
    ).start()
    with pytest.raises(BehaviorTreeError) as exc:
        actor.tick(None)
    assert isinstance(exc.value.__cause__, TypeError)
    actor = BehaviorTree(
        Condition("bad", "bad"), actions={}, conditions={"bad": lambda c: 1}
    ).start()
    with pytest.raises(BehaviorTreeError):
        actor.tick(None)

    def interrupt(context):
        raise KeyboardInterrupt()

    actor = BehaviorTree(
        Condition("bad", "bad"), actions={}, conditions={"bad": interrupt}
    ).start()
    with pytest.raises(KeyboardInterrupt):
        actor.tick(None)
    actor.cancel(None)


def test_trace_exact_order_and_private_context_and_cancel():
    trace = InMemoryTrace()
    actor = BehaviorTree(
        Branch("decision", "ok", Action("yes", "work"), Action("no", "work")),
        actions={"work": Work(3)},
        conditions={"ok": lambda c: True},
        trace=trace,
    ).start()
    actor.tick({"secret": "private-value"})
    assert [event.name for event in trace.events] == [
        "behavior_tree.node_entered",
        "behavior_tree.condition",
        "behavior_tree.branch",
        "behavior_tree.node_entered",
        "behavior_tree.node_finished",
        "behavior_tree.node_finished",
        "behavior_tree.tick_finished",
    ]
    assert trace.events[2].attributes["branch"] == "yes"
    assert "private-value" not in repr([event.attributes for event in trace.events])
    actor.cancel(None)
    assert trace.events[-1].name == "behavior_tree.cancelled"


def test_trace_failure_blocks_next_tick_without_reexecuting_action():
    class BrokenTrace:
        def record(self, event, payload):
            if event.value == "behavior_tree.tick_finished":
                raise OSError("trace unavailable")

    work = Work(3)
    actor = BehaviorTree(
        Action("work", "work"),
        actions={"work": work},
        conditions={},
        trace=BrokenTrace(),
    ).start()
    with pytest.raises(OSError):
        actor.tick(None)
    with pytest.raises(RuntimeError):
        actor.tick(None)
    actor.cancel(None)
    assert len(work.calls) == 1 and len(work.cancellations) == 1


def test_reentrant_tick_is_rejected_before_second_effect():
    class Reentrant(Work):
        def tick(self, context, memory):
            actor.tick(context)
            return Status.SUCCESS

    actor = BehaviorTree(
        Action("work", "work"), actions={"work": Reentrant()}, conditions={}
    ).start()
    with pytest.raises(BehaviorTreeError) as exc:
        actor.tick(None)
    assert isinstance(exc.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "root",
    [
        Selector("root", ()),
        Sequence("root", []),
        Sequence("root", (Condition("same", "ok"), Condition("same", "ok"))),
        Condition("", "ok"),
        Condition("bad", "missing"),
        Action("bad", "missing"),
    ],
)
def test_invalid_definitions(root):
    with pytest.raises((ValueError, TypeError)):
        BehaviorTree(root, actions={}, conditions={"ok": lambda c: True})


def test_definition_codec_strict_and_registration_snapshot():
    handler = Work()
    actions = {"work": handler}
    tree = BehaviorTree(Action("work", "work"), actions=actions, conditions={})
    actions.clear()
    assert tree.start().tick(None) == Status.SUCCESS
    for change in (
        lambda d: d.update(version=True),
        lambda d: d["root"].update(extra=0),
        lambda d: d["root"].update(type="eval"),
    ):
        data = copy.deepcopy(tree.to_data())
        change(data)
        with pytest.raises(ValueError):
            BehaviorTree.from_data(data, actions={"work": handler}, conditions={})


def test_condition_sequence_short_circuits_and_selector_exhausts():
    never = Work()
    tree = BehaviorTree(
        Selector(
            "root",
            (
                Sequence(
                    "seq", (Condition("false", "false"), Action("never", "never"))
                ),
                Inverter("invert", Condition("true", "true")),
            ),
        ),
        actions={"never": never},
        conditions={"false": lambda c: False, "true": lambda c: True},
    )
    assert tree.start().tick(None) == Status.FAILURE
    assert not never.calls


def test_framework_does_not_schedule_or_run_other_tree_during_interruption():
    low, high = Work(5), Work()
    running = BehaviorTree(
        Action("low", "work"), actions={"work": low}, conditions={}
    ).start()
    urgent = BehaviorTree(
        Action("high", "work"), actions={"work": high}, conditions={}
    ).start()
    assert running.tick(None) == Status.RUNNING
    assert not high.calls
    running.cancel(None)
    assert low.cancellations == [(None, {"progress": 1})]
    assert urgent.tick(None) == Status.SUCCESS
    assert len(low.calls) == 1
    assert running.tick(None) == Status.RUNNING
    assert running.snapshot()["memory"]["low"]["progress"] == 1


def test_bounds_are_checked_for_native_and_serialized_definitions():
    node = Condition("leaf", "ok")
    for index in range(64):
        node = Inverter(f"n{index}", node)
    with pytest.raises(ValueError, match="64"):
        BehaviorTree(node, actions={}, conditions={"ok": lambda c: True})
    nodes = tuple(Condition(f"n{index}", "ok") for index in range(1024))
    with pytest.raises(ValueError, match="1024"):
        BehaviorTree(
            Selector("root", nodes), actions={}, conditions={"ok": lambda c: True}
        )
    data = {
        "version": 1,
        "root": {
            "id": "root",
            "type": "selector",
            "children": [
                {"id": f"n{index}", "type": "condition", "predicate": "ok"}
                for index in range(1024)
            ],
        },
    }
    with pytest.raises(ValueError, match="1024"):
        BehaviorTree.from_data(data, actions={}, conditions={"ok": lambda c: True})


def test_identical_execution_replays_have_identical_data_and_trace():
    def replay():
        trace = InMemoryTrace()
        tree = BehaviorTree(
            Branch("decision", "ok", Action("yes", "work"), Action("no", "work")),
            actions={"work": Work(3)},
            conditions={"ok": lambda c: c},
            trace=trace,
        )
        actor = tree.start()
        states = []
        for context in (True, False, True, False):
            result = actor.tick(context)
            states.append((result.value, actor.snapshot()))
        return (
            tree.to_data(),
            states,
            [(event.name, dict(event.attributes)) for event in trace.events],
        )

    assert replay() == replay()


def test_native_example_reports_running_interruption_and_completion():
    from examples.native_behavior_tree import run_example

    assert run_example() == {
        "first": "running",
        "interrupted": "running",
        "completed": "success",
    }


def test_native_outcome_codec_checkpoint_and_cancel_followup_only():
    from bbagents.behavior_trees import Outcome

    first, followup = Work(result=Status.FAILURE), Work(5)
    bindings = {"first": first, "followup": followup}
    tree = BehaviorTree(
        Outcome(
            "result", Action("first", "first"), failure=Action("followup", "followup")
        ),
        actions=bindings,
        conditions={},
    )
    run = tree.start()
    assert run.tick(None) == Status.RUNNING
    rebuilt = BehaviorTree.from_data(tree.to_data(), actions=bindings, conditions={})
    run = Execution.restore(rebuilt, run.snapshot())
    assert run.tick(None) == Status.RUNNING
    assert len(first.calls) == 1
    run.cancel(None)
    assert not first.cancellations
    assert followup.cancellations == [(None, {"progress": 2})]
    assert "outcomes" not in run.snapshot()


def test_native_outcome_without_matching_handler_preserves_child_status():
    from bbagents.behavior_trees import Outcome

    followup = Work()
    tree = BehaviorTree(
        Outcome(
            "result", Action("first", "first"), success=Action("followup", "followup")
        ),
        actions={"first": Work(result=Status.FAILURE), "followup": followup},
        conditions={},
    )
    assert tree.start().tick(None) == Status.FAILURE
    assert not followup.calls


def test_native_outcome_checkpoint_rejects_wrong_phase_or_handler():
    from bbagents.behavior_trees import Outcome

    tree = BehaviorTree(
        Outcome(
            "result", Action("first", "first"), failure=Action("followup", "followup")
        ),
        actions={"first": Work(result=Status.FAILURE), "followup": Work(3)},
        conditions={},
    )
    run = tree.start()
    run.tick(None)
    for wrong in ("running", "success", False):
        state = run.snapshot()
        state["outcomes"]["result"] = wrong
        with pytest.raises(ValueError):
            Execution.restore(tree, state)

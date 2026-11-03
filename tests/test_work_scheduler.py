"""Fair service, cooperative native execution, and exact checkpoint continuity."""

from copy import deepcopy
import json

import pytest

from bbagents.behavior_trees import Status
from bbagents.decision_policies import DecisionCompiler
from bbagents.observability import InMemoryTrace
from bbagents.policy_registry import Event
from bbagents.policy_scheduler import PolicyScheduler, WorkGroup
from test_decision_policies import Work, action, compare, policy, registry


def build(*, duration=1, weights=(1, 1), guards=None, failure=False, units=None):
    handlers = {
        "travel": Work(
            "travel",
            duration=duration,
            result=Status.FAILURE if failure else Status.SUCCESS,
        ),
        "eat": Work("eat", duration=duration),
        "attack": Work("attack"),
    }
    catalog, _ = registry(actions=handlers)
    compiler = DecisionCompiler(catalog)
    policies = [
        compiler.compile(
            policy(
                identity=name,
                root=action(operation=operation),
                scheduling={"group": "work", "weight": weight},
                **({"when": guards} if guards and name == "first" else {}),
            )
        )
        for name, operation, weight in zip(
            ("first", "second"), ("travel", "eat"), weights
        )
    ]
    policies.append(
        compiler.compile(
            policy("urgent", "danger", action(operation="attack"), priority=100)
        )
    )
    trace = InMemoryTrace()
    options = {
        "groups": {"work": WorkGroup(20, quantum_steps=2, failure_backoff_steps=3)},
        "work_units": units,
        "trace": trace,
    }
    return (
        catalog,
        policies,
        PolicyScheduler(catalog, policies, **options),
        handlers,
        options,
        trace,
    )


@pytest.mark.parametrize("duration", [1, 20])
def test_weighted_service_rotates_short_and_long_jobs_without_restart(duration):
    _, _, scheduler, handlers, _, _ = build(duration=duration, weights=(2, 1))
    chosen = [scheduler.tick({}, step=i).policy for i in range(18)]
    assert (
        chosen
        == ["first"] * 4
        + ["second"] * 2
        + ["first"] * 4
        + ["second"] * 2
        + ["first"] * 4
        + ["second"] * 2
    )
    assert len(handlers["travel"].calls) == 12 and len(handlers["eat"].calls) == 6
    assert not handlers["travel"].cancelled and not handlers["eat"].cancelled
    assert scheduler.snapshot()["work"]["service"] == {"first": 12, "second": 6}


def test_emergency_preempts_and_exact_work_memory_resumes():
    _, _, scheduler, handlers, _, _ = build(duration=3)
    assert scheduler.tick({}, step=0).policy == "first"
    assert scheduler.tick({"danger": True}, step=1).policy == "urgent"
    assert not handlers["travel"].cancelled
    assert scheduler.tick({}, step=2).policy == "first"
    assert scheduler.tick({}, step=3).policy == "second"
    assert scheduler.tick({}, step=4).policy == "second"
    result = scheduler.tick({}, step=5)
    assert result.policy == "first" and result.status == Status.SUCCESS


def test_guard_skips_work_without_effects_and_without_inactive_service_debt():
    _, _, scheduler, handlers, _, _ = build(guards=compare("health", ">", 80))
    for i in range(10):
        assert scheduler.tick({"health": 10}, step=i).policy == "second"
    assert not handlers["travel"].calls
    assert scheduler.tick({"health": 90}, step=10).policy == "first"
    assert scheduler.tick({"health": 90}, step=11).policy == "first"
    assert scheduler.tick({"health": 90}, step=12).policy == "second"


def test_failure_backoff_does_not_rotate_on_artificial_tool_failure():
    _, _, scheduler, _, _, _ = build(failure=True)
    assert scheduler.tick({}, step=0).status == Status.FAILURE
    for i in range(1, 4):
        assert scheduler.tick({}, step=i).policy == "second"
    assert scheduler.tick({}, step=4).policy == "second"  # Finish its current turn.
    assert scheduler.tick({}, step=5).policy == "first"


def test_zero_service_is_bounded_and_does_not_starve_other_work():
    _, _, scheduler, _, _, _ = build(duration=100, units=lambda frame: 0)
    assert [scheduler.tick({}, step=i).policy for i in range(6)] == [
        "first",
        "first",
        "second",
        "second",
        "first",
        "first",
    ]
    assert scheduler.snapshot()["work"]["service"] == {"first": 0, "second": 0}


def test_restore_covers_suspended_progress_cursor_and_service():
    catalog, policies, scheduler, _, options, _ = build(duration=5, weights=(2, 1))
    for i in range(7):
        scheduler.tick({}, step=i)
    state = json.loads(json.dumps(scheduler.snapshot()))
    restored = PolicyScheduler.restore(catalog, policies, state, **options)
    for i in range(7, 31):
        context = {"danger": i in {8, 9, 21}}
        assert scheduler.tick(context, step=i) == restored.tick(context, step=i)
        assert scheduler.snapshot() == restored.snapshot()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["work"]["cursor"].update(work=999),
        lambda s: s["work"]["service"].update(first=-1),
        lambda s: s["work"]["used"].update(work=True),
        lambda s: s["work"]["parked"].update(unknown={}),
        lambda s: s.update(version=1),
    ],
)
def test_invalid_fair_checkpoint_is_rejected(change):
    catalog, policies, scheduler, _, options, _ = build(duration=5)
    for i in range(3):
        scheduler.tick({}, step=i)
    state = deepcopy(scheduler.snapshot())
    change(state)
    with pytest.raises(ValueError):
        PolicyScheduler.restore(catalog, policies, state, **options)


def test_parked_occurrence_reserves_queue_capacity_and_cancel_releases_all_work():
    catalog, handlers = registry(
        actions={
            "travel": Work("travel", duration=100),
            "eat": Work("eat", duration=100),
        }
    )
    compiler = DecisionCompiler(catalog)
    policies = [
        compiler.compile(
            policy(
                name,
                "spotted",
                action(operation=operation),
                scheduling={"group": "work", "weight": 1},
            )
        )
        for name, operation in (("first", "travel"), ("second", "eat"))
    ]
    scheduler = PolicyScheduler(
        catalog, policies, groups={"work": WorkGroup(20, 1)}, max_pending=1
    )
    scheduler.tick(
        {}, step=0, events=[Event("spotted", 0, {"entity": "tree", "distance": 1})]
    )
    with pytest.raises(ValueError, match="capacity"):
        scheduler.tick(
            {}, step=1, events=[Event("spotted", 1, {"entity": "tree", "distance": 1})]
        )
    scheduler.tick({}, step=1)
    scheduler.cancel({})
    assert len(handlers["travel"].cancelled) == len(handlers["eat"].cancelled) == 1


def test_guard_and_scheduling_are_typed_and_preserved_in_canonical_ast():
    catalog, _ = registry()
    compiler = DecisionCompiler(catalog)
    data = policy(scheduling={"group": "work", "weight": 2}, when=compare())
    compiled = compiler.compile(data)
    assert compiled.to_data()["scheduling"] == data["scheduling"]
    assert compiled.to_data()["when"] == data["when"]
    for scheduling in (
        {"group": "work", "weight": True},
        {"group": "work", "weight": 0},
        {"group": "work"},
    ):
        with pytest.raises(ValueError):
            compiler.compile(policy(scheduling=scheduling))
    with pytest.raises(ValueError, match="unconfigured"):
        PolicyScheduler(catalog, [compiled])


def test_guard_release_parks_until_ready_and_restores_native_progress():
    catalog, policies, scheduler, handlers, options, _ = build(
        duration=3, guards=compare("health", ">", 80)
    )
    scheduler.tick({"health": 90}, step=0)
    scheduler.tick({"health": 10}, step=1)
    state = scheduler.snapshot()
    assert "first" in state["work"]["parked"]
    assert not handlers["travel"].cancelled
    restored = PolicyScheduler.restore(catalog, policies, state, **options)
    for i in range(2, 8):
        assert scheduler.tick({"health": 90}, step=i) == restored.tick(
            {"health": 90}, step=i
        )
        assert scheduler.snapshot() == restored.snapshot()


def test_actual_units_not_success_count_bound_a_turn():
    _, _, scheduler, _, _, _ = build(duration=100, units=lambda frame: 2)
    assert [scheduler.tick({}, step=i).policy for i in range(4)] == [
        "first",
        "second",
        "first",
        "second",
    ]
    assert scheduler.snapshot()["work"]["service"] == {"first": 4, "second": 4}


def test_invalid_meter_faults_scheduler_instead_of_repeating_effects():
    _, _, scheduler, handlers, _, _ = build(units=lambda frame: True)
    with pytest.raises(ValueError, match="work_units"):
        scheduler.tick({}, step=0)
    with pytest.raises(RuntimeError, match="faulted"):
        scheduler.tick({}, step=1)
    assert len(handlers["travel"].calls) == 1
    scheduler.cancel({})


def test_configuration_changes_are_not_accepted_by_restore():
    catalog, policies, scheduler, _, options, _ = build()
    scheduler.tick({}, step=0)
    with pytest.raises(ValueError, match="Incompatible"):
        PolicyScheduler.restore(
            catalog,
            policies,
            scheduler.snapshot(),
            **{**options, "groups": {"work": WorkGroup(20, quantum_steps=3)}},
        )

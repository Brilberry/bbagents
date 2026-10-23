"""Behavior selection is deterministic proposal arbitration, never execution."""

from dataclasses import FrozenInstanceError, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bbagents import (
    Action,
    ActionRuntime,
    ActionStatus,
    AuthorizationDecision,
    Plan,
    PlanStep,
    ToolCall,
    ToolRegistry,
)
from bbagents.behavior import (
    Abstain,
    BehaviorPolicy,
    PolicyAttempt,
    PolicyRule,
    PolicySelectionError,
    PolicySelector,
    Proposal,
    Selection,
)
from bbagents.observability import InMemoryTrace, KeyRedactor
from bbagents.planning import StepStatus


@dataclass(frozen=True)
class Snapshot:
    urgent: bool = False


class Controller:
    def __init__(self, result):
        self.result = result
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.result


class Broken:
    def __init__(self, error):
        self.error = error

    def propose(self, context):
        raise self.error


class Trace:
    def __init__(self):
        self.events = []

    def record(self, event, payload=None):
        self.events.append((str(event.value), dict(payload or {})))


def proposal(reason="available"):
    return Proposal(ToolCall("inspect", {"token": "private-argument"}), reason)


def test_structural_policy_accepts_custom_context_without_inheritance():
    class Urgency:
        def propose(self, context: Snapshot) -> Proposal | Abstain:
            return proposal() if context.urgent else Abstain("not urgent")

    policy: BehaviorPolicy[Snapshot] = Urgency()
    selector = PolicySelector([PolicyRule("urgency", policy)])
    assert selector.select(Snapshot()).action is None
    assert selector.select(Snapshot(True)).action == proposal().action


def test_priorities_ties_snapshot_and_short_circuit():
    high = Controller(Abstain("not urgent"))
    first = Controller(proposal())
    tied = Controller(proposal())
    low = Controller(proposal())
    rules = [
        PolicyRule("low", low, -10),
        PolicyRule("first", first, -1),
        PolicyRule("high", high, 10),
        PolicyRule("tied", tied, -1),
    ]
    trace = Trace()
    selector = PolicySelector(rules, trace=trace)
    assert not any(policy.contexts for policy in [high, first, tied, low])
    rules.reverse()
    rules.clear()
    context = Snapshot()
    selected = selector.select(context)
    assert selected.policy == "first"
    assert selected.considered == (
        PolicyAttempt("high", 10, "abstained", "not urgent"),
        PolicyAttempt("first", -1, "selected", "available"),
    )
    assert high.contexts == first.contexts == [context]
    assert high.contexts[0] is first.contexts[0] is context
    assert tied.contexts == low.contexts == []
    assert trace.events == [
        (
            "behavior.policy_evaluated",
            {
                "policy": "high",
                "priority": 10,
                "registration_index": 2,
                "outcome": "abstained",
            },
        ),
        (
            "behavior.policy_evaluated",
            {
                "policy": "first",
                "priority": -1,
                "registration_index": 1,
                "outcome": "selected",
            },
        ),
        (
            "behavior.selected",
            {
                "policy": "first",
                "tool_name": "inspect",
                "attempted_count": 2,
            },
        ),
    ]
    assert selector.select(context) == selected
    assert len(selected.considered) == 2


@pytest.mark.parametrize("count", [0, 2])
def test_explicit_no_match_and_trace(count):
    trace = Trace()
    selector = PolicySelector(
        [
            PolicyRule(f"policy-{i}", Controller(Abstain("nothing ready")))
            for i in range(count)
        ],
        trace=trace,
    )
    selection = selector.select(object())
    assert selection == Selection(
        None,
        None,
        "No policy proposed an action",
        tuple(
            PolicyAttempt(f"policy-{i}", 0, "abstained", "nothing ready")
            for i in range(count)
        ),
    )
    assert trace.events == [
        (
            "behavior.policy_evaluated",
            {
                "policy": f"policy-{i}",
                "priority": 0,
                "registration_index": i,
                "outcome": "abstained",
            },
        )
        for i in range(count)
    ] + [("behavior.no_match", {"attempted_count": count})]


@pytest.mark.parametrize(
    "name, error",
    [
        ("", ValueError),
        (" \t\n", ValueError),
        ("x" * 129, ValueError),
        (None, TypeError),
        (False, TypeError),
        (123, TypeError),
    ],
)
def test_invalid_names(name, error):
    with pytest.raises(error, match="name"):
        PolicySelector([PolicyRule(name, Controller(proposal()))])


def test_duplicate_names_and_maximum_name_length():
    rule = PolicyRule("same", Controller(proposal()))
    with pytest.raises(ValueError, match="Duplicate"):
        PolicySelector([rule, rule])
    assert (
        PolicySelector([PolicyRule("x" * 128, Controller(proposal()))])
        .select(None)
        .policy
        == "x" * 128
    )


@pytest.mark.parametrize("priority", [True, False, 1.5, "1", None])
def test_invalid_priorities(priority):
    with pytest.raises(TypeError, match="priority"):
        PolicySelector([PolicyRule("bad", Controller(proposal()), priority)])


@pytest.mark.parametrize(
    "policy", [None, object(), lambda: proposal(), type("P", (), {"propose": 1})()]
)
def test_missing_or_noncallable_propose(policy):
    with pytest.raises(TypeError, match="callable propose"):
        PolicySelector([PolicyRule("bad", policy)])


@pytest.mark.parametrize("mode", [None, "ignore", True, 1, []])
def test_invalid_error_mode(mode):
    with pytest.raises(ValueError, match="error_mode"):
        PolicySelector([], error_mode=mode)


def test_invalid_constructor_options():
    with pytest.raises(TypeError, match="PolicyRule"):
        PolicySelector([object()])
    with pytest.raises(TypeError, match="trace_reasons"):
        PolicySelector([], trace_reasons="yes")
    with pytest.raises(TypeError, match="callable record"):
        PolicySelector([], trace=object())


@pytest.mark.parametrize("mode", ["raise", "skip"])
@pytest.mark.parametrize(
    "result",
    [
        None,
        True,
        False,
        "private-return",
        object(),
        AuthorizationDecision.allow("private-return"),
        Proposal(Action("wrong"), "reason"),
        Proposal(ToolCall("inspect"), 42),
        Abstain(None),
    ],
)
def test_invalid_contract_never_skipped(mode, result):
    fallback = Controller(proposal())
    trace = Trace()
    selector = PolicySelector(
        [
            PolicyRule("bad", Controller(result)),
            PolicyRule("fallback", fallback),
        ],
        error_mode=mode,
        trace=trace,
    )
    with pytest.raises(TypeError, match="bad.*Contract error") as error:
        selector.select(None)
    assert "private-return" not in str(error.value)
    assert fallback.contexts == []
    assert trace.events == [
        (
            "behavior.policy_evaluated",
            {
                "policy": "bad",
                "priority": 0,
                "registration_index": 0,
                "outcome": "error",
                "error_type": "TypeError",
            },
        )
    ]


def test_awaitable_is_a_contract_error_not_implicitly_awaited():
    async def pending():
        raise AssertionError("must not run")

    coroutine = pending()
    try:
        with pytest.raises(TypeError, match="bad.*coroutine"):
            PolicySelector(
                [
                    PolicyRule("bad", Controller(coroutine)),
                ],
                error_mode="skip",
            ).select(None)
    finally:
        coroutine.close()  # The application, not the selector, owns this object.


def test_default_failure_stops_chains_and_keeps_diagnostics_private():
    error = ValueError("private-exception")
    trace = Trace()
    fallback = Controller(proposal())
    selector = PolicySelector(
        [
            PolicyRule("broken", Broken(error), 10),
            PolicyRule("fallback", fallback),
        ],
        trace=trace,
    )
    with pytest.raises(PolicySelectionError) as caught:
        selector.select({"secret": "private-context"})
    assert caught.value.__cause__ is error
    assert str(caught.value) == "Behavior policy 'broken' raised ValueError"
    assert caught.value.policy == "broken"
    assert caught.value.considered == (
        PolicyAttempt(
            "broken", 10, "error", "Policy evaluation raised ValueError", "ValueError"
        ),
    )
    assert fallback.contexts == []
    assert trace.events == [
        (
            "behavior.policy_evaluated",
            {
                "policy": "broken",
                "priority": 10,
                "registration_index": 0,
                "outcome": "error",
                "error_type": "ValueError",
            },
        )
    ]
    assert "private" not in repr(trace.events)


@pytest.mark.parametrize("has_fallback", [True, False])
def test_explicit_skip_preserves_failure_attempt_and_terminal_trace(has_fallback):
    trace = Trace()
    rules = [PolicyRule("broken", Broken(ValueError("private-exception")))]
    if has_fallback:
        rules.append(PolicyRule("fallback", Controller(proposal())))
    selection = PolicySelector(rules, error_mode="skip", trace=trace).select(None)
    assert selection.considered[0].outcome == "error"
    assert selection.considered[0].error_type == "ValueError"
    assert selection.policy == ("fallback" if has_fallback else None)
    assert trace.events[0] == (
        "behavior.policy_evaluated",
        {
            "policy": "broken",
            "priority": 0,
            "registration_index": 0,
            "outcome": "error",
            "error_type": "ValueError",
        },
    )
    expected = [trace.events[0]]
    if has_fallback:
        expected.extend(
            [
                (
                    "behavior.policy_evaluated",
                    {
                        "policy": "fallback",
                        "priority": 0,
                        "registration_index": 1,
                        "outcome": "selected",
                    },
                ),
                (
                    "behavior.selected",
                    {
                        "policy": "fallback",
                        "tool_name": "inspect",
                        "attempted_count": 2,
                    },
                ),
            ]
        )
    else:
        expected.append(("behavior.no_match", {"attempted_count": 1}))
    assert trace.events == expected
    assert "private-exception" not in repr(selection)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(), GeneratorExit()])
@pytest.mark.parametrize("mode", ["raise", "skip"])
def test_base_exceptions_propagate_without_attempts_or_terminal_events(error, mode):
    trace = Trace()
    with pytest.raises(type(error)) as caught:
        PolicySelector(
            [
                PolicyRule("signal", Broken(error)),
            ],
            error_mode=mode,
            trace=trace,
        ).select(None)
    assert caught.value is error
    assert trace.events == []


@pytest.mark.parametrize("opt_in", [False, True])
def test_reason_privacy_and_shared_trace_adapter(opt_in):
    trace = InMemoryTrace()
    selection = PolicySelector(
        [
            PolicyRule("idle", Controller(Abstain("private-abstention"))),
            PolicyRule("ready", Controller(proposal("private-reason"))),
        ],
        trace=trace,
        trace_reasons=opt_in,
    ).select({"secret": "private-context"})
    assert selection.reason == "private-reason"
    payloads = [dict(event.attributes) for event in trace.events]
    assert ("private-reason" in repr(payloads)) == opt_in
    assert ("private-abstention" in repr(payloads)) == opt_in
    assert "private-context" not in repr(payloads)
    assert "private-argument" not in repr(payloads)
    redacted = InMemoryTrace(redactor=KeyRedactor(frozenset({"reason"})))
    PolicySelector(
        [
            PolicyRule("ready", Controller(proposal("private-reason"))),
        ],
        trace=redacted,
        trace_reasons=True,
    ).select(None)
    assert redacted.events[0].attributes["reason"] == "[REDACTED]"


@pytest.mark.parametrize(
    "result, event",
    [
        (Abstain("idle"), "behavior.policy_evaluated"),
        (proposal(), "behavior.policy_evaluated"),
        (None, "behavior.policy_evaluated"),
        (ValueError("secret"), "behavior.policy_evaluated"),
        (proposal(), "behavior.selected"),
        (Abstain("idle"), "behavior.no_match"),
        (ValueError("secret"), "behavior.no_match"),
    ],
)
def test_trace_failures_are_not_swallowed_by_skip(result, event):
    class FailedTrace(Trace):
        def record(self, name, payload=None):
            if name.value == event:
                raise OSError("trace unavailable")
            super().record(name, payload)

    policy = Broken(result) if isinstance(result, Exception) else Controller(result)
    trace = FailedTrace()
    selector = PolicySelector(
        [
            PolicyRule("first", policy),
        ],
        trace=trace,
        error_mode="skip",
    )
    with pytest.raises(OSError, match="trace unavailable"):
        selector.select(None)


def test_trace_failure_during_evaluation_does_not_run_lower_policy():
    class FailedTrace:
        def record(self, event, payload=None):
            raise OSError("trace unavailable")

    lower = Controller(proposal())
    with pytest.raises(OSError):
        PolicySelector(
            [
                PolicyRule("idle", Controller(Abstain("idle"))),
                PolicyRule("lower", lower),
            ],
            trace=FailedTrace(),
            error_mode="skip",
        ).select(None)
    assert lower.contexts == []


def test_policy_raised_type_error_is_not_confused_with_invalid_return():
    selected = PolicySelector(
        [
            PolicyRule("broken", Broken(TypeError("private-exception"))),
            PolicyRule("ready", Controller(proposal())),
        ],
        error_mode="skip",
    ).select(None)
    assert selected.policy == "ready"
    assert selected.considered[0].error_type == "TypeError"


def test_invalid_objects_are_not_rendered_in_diagnostics():
    class Private:
        def __str__(self):
            raise AssertionError("must not stringify private data")

        __repr__ = __str__

    with pytest.raises(TypeError, match="Private"):
        PolicySelector(
            [
                PolicyRule("private", Controller(Private())),
            ],
            error_mode="skip",
        ).select(None)


def test_no_attempt_state_leaks_after_failure_or_context_change():
    class Variable:
        def propose(self, context):
            if context == "broken":
                raise ValueError("failure")
            return Abstain("idle") if context == "idle" else proposal()

    selector = PolicySelector([PolicyRule("variable", Variable())])
    with pytest.raises(PolicySelectionError) as failed:
        selector.select("broken")
    idle = selector.select("idle")
    selected = selector.select("ready")
    assert failed.value.considered[0].outcome == "error"
    assert [attempt.outcome for attempt in idle.considered] == ["abstained"]
    assert [attempt.outcome for attempt in selected.considered] == ["selected"]


def test_shallow_immutability_preserves_argument_and_context_ownership():
    arguments = {"items": []}
    call = ToolCall("not-registered", arguments)
    context = {"plan": Plan("inspect", [PlanStep("inspect", "Inspect")])}
    before = repr(context)
    selector = PolicySelector(
        [PolicyRule("ready", Controller(Proposal(call, "ready")))]
    )
    selection = selector.select(context)
    assert selection.action is call
    assert selection.action.arguments is arguments
    assert repr(context) == before
    assert context["plan"].steps[0].status == StepStatus.PENDING
    assert not context["plan"].steps[0].evidence
    for value in [
        Proposal(call, "ready"),
        Abstain("idle"),
        PolicyRule("ready", Controller(proposal())),
        selection.considered[0],
        selection,
    ]:
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            value.reason = "changed"


def test_no_execution_models_clocks_rng_or_plan_completion(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("selector initiated external work")

    import random
    import time
    import uuid
    from bbagents.decision import DecisionRuntime

    monkeypatch.setattr(ToolRegistry, "execute", forbidden)
    monkeypatch.setattr(DecisionRuntime, "decide", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "monotonic", forbidden)
    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(uuid, "uuid4", forbidden)
    plan = Plan("inspect", [PlanStep("inspect", "Inspect")])
    context = {"plan": plan}
    selected = PolicySelector([PolicyRule("ready", Controller(proposal()))]).select(
        context
    )
    assert selected.action is not None
    assert plan.steps[0].status == StepStatus.PENDING
    assert not plan.steps[0].evidence


def test_authorization_denial_does_not_execute_or_select_fallback():
    class Deny:
        def decide(self, action):
            return AuthorizationDecision.deny("actor lacks permission")

    effects = []
    fallback = Controller(proposal())
    selected = PolicySelector(
        [
            PolicyRule("release", Controller(Proposal(ToolCall("publish"), "ready"))),
            PolicyRule("fallback", fallback),
        ]
    ).select(None)
    result = ActionRuntime(
        {"publish": lambda: effects.append("published")},
        policy=Deny(),
    ).execute(Action(selected.action.tool_name))  # Application-owned adaptation.
    assert result.status == ActionStatus.DENIED
    assert effects == fallback.contexts == []
    assert len(selected.considered) == 1


def test_process_hash_seeds_do_not_change_selection():
    script = """
import json
from dataclasses import asdict
from bbagents import ToolCall
from bbagents.behavior import Abstain, Proposal, PolicyRule, PolicySelector
class Idle:
    def propose(self, context): return Abstain("idle")
class Ready:
    def propose(self, context): return Proposal(ToolCall("inspect"), "ready")
rules = [PolicyRule("low", Ready(), -1), PolicyRule("high", Idle(), 100),
         PolicyRule("first", Ready(), 20), PolicyRule("tied", Ready(), 20)]
print(json.dumps(asdict(PolicySelector(rules).select(None)), sort_keys=True))
"""
    outputs = []
    for seed in ["0", "1", "42", "random"]:
        env = dict(
            os.environ,
            PYTHONHASHSEED=seed,
            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        )
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                env=env,
                text=True,
                timeout=15,
            )
        )
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])["policy"] == "first"


def test_offline_example_has_application_owned_feedback():
    from examples.behavior_policy_selection import run_example

    selection, plan, observation, trace = run_example()
    assert selection.policy == "available_plan"
    assert [attempt.outcome for attempt in selection.considered] == [
        "abstained",
        "selected",
    ]
    assert plan.is_complete()
    assert plan.steps[0].evidence == [observation.content]
    assert len(trace.events) == 3

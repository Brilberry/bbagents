"""Prioritized proposal selection, independent of authorization and execution.

Policies are trusted synchronous functions, not sandboxed code. Context and tool
arguments are application-owned snapshots: frozen values below are only shallowly
immutable. Selection neither copies context nor advances controller lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterable, Literal, Protocol, TypeVar

from .core import NullTrace, TraceSink
from .observability import TraceEventName
from .tools import ToolCall

ContextT = TypeVar("ContextT", contravariant=True)


class BehaviorPolicy(Protocol[ContextT]):
    """Inspect context and explicitly propose or abstain, without executing work."""

    def propose(self, context: ContextT) -> Proposal | Abstain: ...


@dataclass(frozen=True, slots=True)
class Proposal:
    """A candidate, not permission or execution evidence; validated on selection."""

    action: ToolCall
    reason: str


@dataclass(frozen=True, slots=True)
class Abstain:
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyRule(Generic[ContextT]):
    name: str
    policy: BehaviorPolicy[ContextT]
    priority: int = 0


@dataclass(frozen=True, slots=True)
class PolicyAttempt:
    policy: str
    priority: int
    outcome: Literal["abstained", "selected", "error"]
    reason: str
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class Selection:
    policy: str | None
    action: ToolCall | None
    reason: str
    considered: tuple[PolicyAttempt, ...]


class PolicySelectionError(RuntimeError):
    """A controller failed; its exception is chained, never rendered as text."""

    def __init__(
        self, policy: str, error_type: str, considered: tuple[PolicyAttempt, ...]
    ) -> None:
        self.policy = policy
        self.considered = considered
        super().__init__(f"Behavior policy {policy!r} raised {error_type}")


class PolicySelector(Generic[ContextT]):
    """Select the first proposal in descending priority, breaking ties by input order.

    ``skip`` deliberately allows bypassing broken controllers; contract violations
    and tracing failures still raise. Policies should be fast and nonblocking.
    Purity and thread safety of application policies are not enforced.

    Reasons remain in returned attempts, but are not traced unless explicitly
    enabled. Opt-in reasons may contain secrets; use a suitable trace redactor.
    """

    def __init__(
        self,
        rules: Iterable[PolicyRule[ContextT]],
        *,
        error_mode: Literal["raise", "skip"] = "raise",
        trace: TraceSink | None = None,
        trace_reasons: bool = False,
    ) -> None:
        if error_mode not in ("raise", "skip"):
            raise ValueError("error_mode must be 'raise' or 'skip'")
        if not isinstance(trace_reasons, bool):
            raise TypeError("trace_reasons must be bool")
        if trace is not None and not callable(getattr(trace, "record", None)):
            raise TypeError("trace must provide a callable record method")
        registered = tuple(enumerate(rules))
        names: set[str] = set()
        for index, rule in registered:
            if not isinstance(rule, PolicyRule):
                raise TypeError(
                    f"Rule at registration index {index} must be PolicyRule"
                )
            if not isinstance(rule.name, str):
                raise TypeError("Policy rule name must be a string")
            if not rule.name.strip() or len(rule.name) > 128:
                raise ValueError(
                    "Policy rule name must be nonblank and at most 128 characters"
                )
            if rule.name in names:
                raise ValueError(f"Duplicate policy rule name: {rule.name!r}")
            names.add(rule.name)
            if isinstance(rule.priority, bool) or not isinstance(rule.priority, int):
                raise TypeError(
                    f"Policy {rule.name!r} priority must be an integer, not bool"
                )
            if not callable(getattr(rule.policy, "propose", None)):
                raise TypeError(
                    f"Policy {rule.name!r} must provide a callable propose method"
                )
        self._rules = tuple(sorted(registered, key=lambda item: -item[1].priority))
        self._error_mode = error_mode
        self._trace = NullTrace() if trace is None else trace
        self._trace_reasons = trace_reasons

    def select(self, context: ContextT) -> Selection:
        attempts: list[PolicyAttempt] = []
        for index, rule in self._rules:
            try:
                candidate = rule.policy.propose(context)
            except Exception as exc:
                error_type = type(exc).__name__
                attempt = PolicyAttempt(
                    rule.name,
                    rule.priority,
                    "error",
                    f"Policy evaluation raised {error_type}",
                    error_type,
                )
                attempts.append(attempt)
                self._evaluated(index, attempt)
                if self._error_mode == "raise":
                    raise PolicySelectionError(
                        rule.name, error_type, tuple(attempts)
                    ) from exc
                continue

            # Validation/tracing deliberately sit outside the evaluation catch.
            violation = _contract_violation(candidate)
            if violation is not None:
                attempt = PolicyAttempt(
                    rule.name,
                    rule.priority,
                    "error",
                    violation,
                    "TypeError",
                )
                attempts.append(attempt)
                self._evaluated(index, attempt)
                raise TypeError(f"Behavior policy {rule.name!r}: {violation}")
            attempt = PolicyAttempt(
                rule.name,
                rule.priority,
                "selected" if isinstance(candidate, Proposal) else "abstained",
                candidate.reason,
            )
            attempts.append(attempt)
            self._evaluated(index, attempt)
            if isinstance(candidate, Proposal):
                self._trace.record(
                    TraceEventName.BEHAVIOR_SELECTED,
                    {
                        "policy": rule.name,
                        "tool_name": candidate.action.tool_name,
                        "attempted_count": len(attempts),
                    },
                )
                return Selection(
                    rule.name, candidate.action, candidate.reason, tuple(attempts)
                )
        self._trace.record(
            TraceEventName.BEHAVIOR_NO_MATCH, {"attempted_count": len(attempts)}
        )
        return Selection(None, None, "No policy proposed an action", tuple(attempts))

    def _evaluated(self, index: int, attempt: PolicyAttempt) -> None:
        payload: dict[str, Any] = {
            "policy": attempt.policy,
            "priority": attempt.priority,
            "registration_index": index,
            "outcome": attempt.outcome,
        }
        if attempt.error_type is not None:
            payload["error_type"] = attempt.error_type
        if self._trace_reasons:
            payload["reason"] = attempt.reason
        self._trace.record(TraceEventName.BEHAVIOR_POLICY_EVALUATED, payload)


def _contract_violation(candidate: object) -> str | None:
    if not isinstance(candidate, (Proposal, Abstain)):
        return (
            "Contract error: propose() must return Proposal or Abstain, "
            f"got {type(candidate).__name__}"
        )
    if not isinstance(candidate.reason, str):
        return (
            f"Contract error: reason must be str, got {type(candidate.reason).__name__}"
        )
    if isinstance(candidate, Proposal) and not isinstance(candidate.action, ToolCall):
        return (
            "Contract error: Proposal.action must be ToolCall, "
            f"got {type(candidate.action).__name__}"
        )
    return None

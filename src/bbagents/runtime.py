"""Policy-aware action execution with retries and structured results."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    expected_effect: str = ""
    requires_confirmation: bool = False
    intervention_id: str | None = None
    timeout_seconds: float = 30.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ActionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    PENDING_INTERVENTION = "pending_intervention"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """The result of attempting an action.

    ``status`` is authoritative. ``success`` is a derived convenience property so
    callers cannot construct contradictory action results.
    """

    status: ActionStatus
    output: Any = None
    error: str | None = None
    attempts: int = 1
    duration_seconds: float = 0.0
    intervention_request_id: str | None = None

    @property
    def success(self) -> bool:
        return self.status == ActionStatus.SUCCEEDED


class AuthorizationStatus(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    PENDING_INTERVENTION = "pending_intervention"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """A portable policy outcome for an action or application subject."""

    status: AuthorizationStatus
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    intervention_request_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == AuthorizationStatus.ALLOWED

    @property
    def pending(self) -> bool:
        return self.status == AuthorizationStatus.PENDING_INTERVENTION

    @classmethod
    def allow(
        cls, reason: str, *, metadata: Mapping[str, Any] | None = None
    ) -> "AuthorizationDecision":
        return cls(AuthorizationStatus.ALLOWED, reason, metadata or {})

    @classmethod
    def deny(
        cls, reason: str, *, metadata: Mapping[str, Any] | None = None
    ) -> "AuthorizationDecision":
        return cls(AuthorizationStatus.DENIED, reason, metadata or {})

    @classmethod
    def pending_intervention(
        cls,
        reason: str,
        intervention_request_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> "AuthorizationDecision":
        return cls(
            AuthorizationStatus.PENDING_INTERVENTION,
            reason,
            metadata or {},
            intervention_request_id,
        )


# Compatibility name for the former action-only decision type.
ActionPolicyDecision = AuthorizationDecision


class ActionPolicy(Protocol):
    def decide(self, action: Action) -> AuthorizationDecision: ...


class LegacyActionPolicy(Protocol):
    """Compatibility protocol for pre-0.2 boolean action policies."""

    def authorize(self, action: Action) -> bool: ...


# Compatibility import path. New code should use ``ActionPolicy``.
DecidingActionPolicy = ActionPolicy


class AllowAllPolicy:
    def decide(self, action: Action) -> AuthorizationDecision:
        del action
        return AuthorizationDecision.allow("action authorized")


class CompositeActionPolicy:
    """Require every policy to permit an action, preserving pending interventions."""

    def __init__(self, policies: list[ActionPolicy | LegacyActionPolicy]) -> None:
        self.policies = tuple(policies)

    def decide(self, action: Action) -> AuthorizationDecision:
        for policy in self.policies:
            decision = _evaluate_policy(policy, action)
            if not decision.allowed:
                return decision
        return AuthorizationDecision.allow("all policies permit action")


ActionHandler = Callable[..., Any]


class ActionRuntime:
    def __init__(
        self,
        handlers: Mapping[str, ActionHandler],
        *,
        policy: ActionPolicy | LegacyActionPolicy | None = None,
    ) -> None:
        self.handlers = dict(handlers)
        self.policy = policy or AllowAllPolicy()

    def execute(self, action: Action, *, retries: int = 0) -> ActionResult:
        started = time.monotonic()
        decision = _evaluate_policy(self.policy, action)
        if not decision.allowed:
            return ActionResult(
                ActionStatus.PENDING_INTERVENTION
                if decision.pending
                else ActionStatus.DENIED,
                error=decision.reason,
                attempts=0,
                intervention_request_id=decision.intervention_request_id,
            )
        handler = self.handlers.get(action.name)
        if handler is None:
            return ActionResult(
                ActionStatus.FAILED,
                error=f"Unknown action: {action.name}",
                attempts=0,
            )
        error: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                output = handler(**dict(action.parameters))
                return ActionResult(
                    ActionStatus.SUCCEEDED,
                    output=output,
                    attempts=attempt,
                    duration_seconds=time.monotonic() - started,
                )
            except Exception as exc:
                error = exc
        return ActionResult(
            ActionStatus.FAILED,
            error=str(error),
            attempts=retries + 1,
            duration_seconds=time.monotonic() - started,
        )


def _evaluate_policy(
    policy: ActionPolicy | LegacyActionPolicy,
    action: Action,
) -> AuthorizationDecision:
    decide = getattr(policy, "decide", None)
    if callable(decide):
        candidate = decide(action)
        if isinstance(candidate, AuthorizationDecision):
            return candidate
        raise TypeError("Action policy decide() must return AuthorizationDecision")
    authorize = getattr(policy, "authorize", None)
    if callable(authorize) and authorize(action):
        return AuthorizationDecision.allow("action authorized")
    return AuthorizationDecision.deny("Action denied by policy")

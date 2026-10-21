"""Capability controls, trust labels, approvals, and audit decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Collection, Mapping, Protocol, TypeVar

from .provenance import TrustLevel as TrustLevel
from .runtime import Action, ActionPolicy, AuthorizationDecision

SubjectT = TypeVar("SubjectT", contravariant=True)
PolicyContextT = TypeVar("PolicyContextT", contravariant=True)


@dataclass(frozen=True, slots=True)
class Capability:
    action: str
    resource: str = "*"


class Policy(Protocol[SubjectT, PolicyContextT]):
    """Application-defined policy evaluated against an explicit context."""

    def decide(
        self, subject: SubjectT, context: PolicyContextT
    ) -> AuthorizationDecision: ...


class PolicyDenied(RuntimeError):
    """Raised when an application chooses to enforce a denied policy decision."""


def enforce(
    policy: Policy[SubjectT, PolicyContextT],
    subject: SubjectT,
    context: PolicyContextT,
) -> AuthorizationDecision:
    """Evaluate a domain policy and raise with its explanation when denied."""

    decision = policy.decide(subject, context)
    if not decision.allowed:
        raise PolicyDenied(decision.reason)
    return decision


@dataclass(slots=True)
class CapabilityPolicy(ActionPolicy):
    """Default-deny action policy with optional legacy static approvals."""

    capabilities: Collection[Capability]
    approvals: Collection[str] = field(default_factory=set)
    enforce_confirmation: bool = True
    audit_log: list[Mapping[str, Any]] = field(default_factory=list)

    def decide(self, action: Action) -> AuthorizationDecision:
        resource = str(action.metadata.get("resource", "*"))
        permitted = any(
            cap.action == action.name and cap.resource in {"*", resource}
            for cap in self.capabilities
        )
        if not permitted:
            decision = AuthorizationDecision.deny("capability not granted")
        elif (
            self.enforce_confirmation
            and action.requires_confirmation
            and action.name not in self.approvals
        ):
            decision = AuthorizationDecision.deny("human approval required")
        else:
            decision = AuthorizationDecision.allow("capability granted")
        self.audit_log.append(
            {
                "action": action.name,
                "allowed": decision.allowed,
                "reason": decision.reason,
            }
        )
        return decision

    def authorize(self, action: Action) -> bool:
        return self.decide(action).allowed


# Compatibility import path. New code should use ``AuthorizationDecision``.
PolicyDecision = AuthorizationDecision

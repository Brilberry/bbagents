"""Portable, versioned human-intervention contracts for governed agent actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .persistence import ConcurrentWriteError
from .provenance import Provenance
from .runtime import Action, AuthorizationDecision


class InterventionKind(str, Enum):
    APPROVAL = "approval"
    INPUT = "input"
    DECISION = "decision"


class InterventionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESPONDED = "responded"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InterventionDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    PROVIDE_INPUT = "provide_input"
    EDIT = "edit"


class ActorKind(str, Enum):
    HUMAN = "human"
    DELEGATE = "delegate"


@dataclass(frozen=True, slots=True)
class InterventionRequest:
    """An immutable, versioned request bound to one proposed action.

    ``action_parameters`` must contain JSON-compatible values if the request is
    exchanged across processes. The fingerprint protects an approval from replay
    against a different action.
    """

    id: str
    kind: InterventionKind
    action_name: str
    resource: str
    summary: str
    action_parameters: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    authority: str = "approve_action"
    subject_ref: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1
    action_fingerprint: str = ""
    provenance: Provenance = field(
        default_factory=lambda: Provenance.system("intervention")
    )

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        fingerprint = _fingerprint(
            self.action_name,
            self.resource,
            self.action_parameters,
        )
        if self.action_fingerprint and self.action_fingerprint != fingerprint:
            raise ValueError("action_fingerprint does not match the action payload")
        object.__setattr__(self, "action_fingerprint", fingerprint)

    def is_expired(self, at: datetime | None = None) -> bool:
        current = at or datetime.now(UTC)
        return self.expires_at is not None and self.expires_at <= current


@dataclass(frozen=True, slots=True)
class InterventionResponse:
    actor_id: str
    actor_kind: ActorKind
    decision: InterventionDecision
    request_version: int
    rationale: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def approved(self) -> bool:
        """Compatibility convenience for approval-only callers."""

        return self.decision == InterventionDecision.APPROVE


@dataclass(frozen=True, slots=True)
class Delegation:
    delegator_id: str
    delegate_id: str
    authority: str
    resource: str = "*"
    expires_at: datetime | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    revoked_at: datetime | None = None

    def is_active(self, at: datetime | None = None) -> bool:
        current = at or datetime.now(UTC)
        return (
            self.revoked_at is None
            and (self.expires_at is None or self.expires_at > current)
        )


@dataclass(frozen=True, slots=True)
class InterventionRecord:
    request: InterventionRequest
    status: InterventionStatus = InterventionStatus.PENDING
    response: InterventionResponse | None = None
    version: int = 1
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InterventionStore(Protocol):
    """Durable request repository with optimistic-concurrency resolution."""

    def create(self, request: InterventionRequest) -> InterventionRecord: ...

    def get(self, request_id: str) -> InterventionRecord | None: ...

    def resolve(
        self,
        request_id: str,
        response: InterventionResponse,
        *,
        expected_version: int,
    ) -> InterventionRecord: ...

    def revoke(
        self, request_id: str, *, expected_version: int
    ) -> InterventionRecord: ...

    def expire(
        self, request_id: str, *, expected_version: int
    ) -> InterventionRecord: ...


class InterventionChannel(Protocol):
    """Bidirectional application adapter between an agent and a human interface."""

    def deliver(self, request: InterventionRequest) -> None: ...

    def receive_response(self, request_id: str) -> InterventionResponse | None: ...


class AuthorityResolver(Protocol):
    """Application policy for human identity and explicitly delegated authority."""

    def permits(
        self,
        request: InterventionRequest,
        response: InterventionResponse,
    ) -> bool: ...


class InMemoryInterventionStore:
    """Reference repository with the same version semantics as ``StateStore``."""

    def __init__(self) -> None:
        self._records: dict[str, InterventionRecord] = {}

    def create(self, request: InterventionRequest) -> InterventionRecord:
        if request.id in self._records:
            raise ValueError(f"Intervention request already exists: {request.id}")
        record = InterventionRecord(request)
        self._records[request.id] = record
        return record

    def get(self, request_id: str) -> InterventionRecord | None:
        return self._records.get(request_id)

    def resolve(
        self,
        request_id: str,
        response: InterventionResponse,
        *,
        expected_version: int,
    ) -> InterventionRecord:
        record = self._require_pending(request_id, expected_version)
        if response.request_version != record.version:
            raise ConcurrentWriteError(
                f"Intervention {request_id!r} response targets version "
                f"{response.request_version}, not {record.version}"
            )
        status = _status_for(record.request, response)
        return self._replace(record, status=status, response=response)

    def revoke(
        self, request_id: str, *, expected_version: int
    ) -> InterventionRecord:
        return self._replace(
            self._require_pending(request_id, expected_version),
            status=InterventionStatus.REVOKED,
        )

    def expire(
        self, request_id: str, *, expected_version: int
    ) -> InterventionRecord:
        record = self._require_pending(request_id, expected_version)
        if not record.request.is_expired():
            raise ValueError("intervention request has not expired")
        return self._replace(record, status=InterventionStatus.EXPIRED)

    def _require_pending(
        self, request_id: str, expected_version: int
    ) -> InterventionRecord:
        record = self._records.get(request_id)
        if record is None:
            raise KeyError(f"Unknown intervention request: {request_id}")
        if record.version != expected_version:
            raise ConcurrentWriteError(
                f"Intervention {request_id!r} is at version {record.version}, "
                f"not {expected_version}"
            )
        if record.status != InterventionStatus.PENDING:
            raise ValueError(f"Intervention request is already {record.status.value}")
        return record

    def _replace(
        self,
        record: InterventionRecord,
        *,
        status: InterventionStatus,
        response: InterventionResponse | None = None,
    ) -> InterventionRecord:
        updated = InterventionRecord(
            record.request,
            status,
            response,
            version=record.version + 1,
        )
        self._records[record.request.id] = updated
        return updated


class InMemoryInterventionChannel:
    """Reference channel for tests and a local human-intervention interface."""

    def __init__(self) -> None:
        self.requests: list[InterventionRequest] = []
        self.responses: dict[str, InterventionResponse] = {}

    def deliver(self, request: InterventionRequest) -> None:
        self.requests.append(request)

    def submit_response(
        self, request_id: str, response: InterventionResponse
    ) -> None:
        if not any(request.id == request_id for request in self.requests):
            raise KeyError(f"Unknown intervention request: {request_id}")
        self.responses[request_id] = response

    def receive_response(self, request_id: str) -> InterventionResponse | None:
        return self.responses.get(request_id)


class HumanOrDelegatedAuthority:
    """Permit a trusted human or an active explicit delegation for the request."""

    def __init__(self, delegations: list[Delegation] | None = None) -> None:
        self.delegations = delegations or []

    def permits(
        self,
        request: InterventionRequest,
        response: InterventionResponse,
    ) -> bool:
        if response.actor_kind == ActorKind.HUMAN:
            return True
        return any(
            delegation.delegate_id == response.actor_id
            and delegation.authority == request.authority
            and delegation.resource in {"*", request.resource}
            and delegation.is_active()
            for delegation in self.delegations
        )


class ActionInterventionService:
    """Create and resolve versioned approval requests for guarded actions."""

    def __init__(
        self,
        store: InterventionStore,
        authority: AuthorityResolver,
        *,
        channel: InterventionChannel | None = None,
    ) -> None:
        self.store = store
        self.authority = authority
        self.channel = channel

    def request(self, action: Action) -> InterventionRequest:
        request = InterventionRequest(
            id=uuid4().hex,
            kind=InterventionKind.APPROVAL,
            action_name=action.name,
            resource=str(action.metadata.get("resource", "*")),
            summary=action.expected_effect or f"Execute {action.name}",
            action_parameters=dict(action.parameters),
            context=dict(action.metadata),
            subject_ref=_optional_text(action.metadata.get("subject_ref")),
            expires_at=_optional_datetime(
                action.metadata.get("intervention_expires_at")
            ),
            provenance=_action_provenance(action),
        )
        self.store.create(request)
        if self.channel is not None:
            self.channel.deliver(request)
        return request

    def respond(
        self, request_id: str, response: InterventionResponse
    ) -> InterventionRecord:
        record = self._current(request_id)
        if not self.authority.permits(record.request, response):
            raise PermissionError(
                "actor is not authorized to resolve this intervention"
            )
        return self.store.resolve(
            request_id,
            response,
            expected_version=record.version,
        )

    def synchronize(self, request_id: str) -> InterventionRecord:
        """Apply a channel response after checking expiry and record version."""

        record = self._current(request_id)
        if record.status != InterventionStatus.PENDING or self.channel is None:
            return record
        response = self.channel.receive_response(request_id)
        if response is None:
            return record
        return self.respond(request_id, response)

    def get(self, request_id: str) -> InterventionRecord:
        return self._current(request_id)

    def decide(self, action: Action) -> AuthorizationDecision:
        if not action.requires_confirmation:
            return AuthorizationDecision.allow("action does not require intervention")
        if action.intervention_id is None:
            request = self.request(action)
            return AuthorizationDecision.pending_intervention(
                "human intervention required", request.id
            )
        record = self.synchronize(action.intervention_id)
        if not self._matches(record.request, action):
            return AuthorizationDecision.deny(
                "intervention request does not match action"
            )
        if record.status == InterventionStatus.PENDING:
            return AuthorizationDecision.pending_intervention(
                "human intervention is pending", record.request.id
            )
        if record.status == InterventionStatus.APPROVED:
            return AuthorizationDecision.allow("human intervention approved")
        return AuthorizationDecision.deny(f"human intervention {record.status.value}")

    def _current(self, request_id: str) -> InterventionRecord:
        record = self.store.get(request_id)
        if record is None:
            raise KeyError(f"Unknown intervention request: {request_id}")
        if record.status == InterventionStatus.PENDING and record.request.is_expired():
            return self.store.expire(request_id, expected_version=record.version)
        return record

    @staticmethod
    def _matches(request: InterventionRequest, action: Action) -> bool:
        return request.action_fingerprint == _fingerprint(
            action.name,
            str(action.metadata.get("resource", "*")),
            action.parameters,
        )


class ActionInterventionPolicy:
    """Adapt intervention state into the action-runtime decision protocol."""

    def __init__(self, service: ActionInterventionService) -> None:
        self.service = service

    def decide(self, action: Action) -> AuthorizationDecision:
        return self.service.decide(action)


def _fingerprint(action_name: str, resource: str, parameters: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"action_name": action_name, "resource": resource, "parameters": parameters},
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _status_for(
    request: InterventionRequest, response: InterventionResponse
) -> InterventionStatus:
    if request.kind == InterventionKind.APPROVAL:
        if response.decision == InterventionDecision.APPROVE:
            return InterventionStatus.APPROVED
        if response.decision in {
            InterventionDecision.REJECT,
            InterventionDecision.CANCEL,
        }:
            return InterventionStatus.REJECTED
        raise ValueError("approval requests require approve, reject, or cancel")
    return InterventionStatus.RESPONDED


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("intervention_expires_at must be a datetime")
    return value


def _action_provenance(action: Action) -> Provenance:
    value = action.metadata.get("provenance")
    if value is None:
        return Provenance.system("intervention")
    if not isinstance(value, Provenance):
        raise TypeError("action provenance must be a Provenance value")
    return value

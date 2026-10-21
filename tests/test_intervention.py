from datetime import UTC, datetime, timedelta

import pytest

from bbagents import Action, ActionRuntime, ActionStatus
from bbagents.intervention import (
    ActionInterventionPolicy,
    ActionInterventionService,
    ActorKind,
    Delegation,
    HumanOrDelegatedAuthority,
    InMemoryInterventionChannel,
    InMemoryInterventionStore,
    InterventionDecision,
    InterventionKind,
    InterventionRequest,
    InterventionResponse,
    InterventionStatus,
)
from bbagents.persistence import ConcurrentWriteError
from bbagents.provenance import Provenance, TrustLevel


def test_action_waits_for_human_intervention_then_resumes() -> None:
    store = InMemoryInterventionStore()
    channel = InMemoryInterventionChannel()
    service = ActionInterventionService(
        store,
        HumanOrDelegatedAuthority(),
        channel=channel,
    )
    runtime = ActionRuntime(
        {"deploy": lambda version: f"deployed {version}"},
        policy=ActionInterventionPolicy(service),
    )
    action = Action("deploy", {"version": "1.2.0"}, requires_confirmation=True)

    pending = runtime.execute(action)

    assert pending.status == ActionStatus.PENDING_INTERVENTION
    assert pending.intervention_request_id is not None
    assert len(channel.requests) == 1

    channel.submit_response(
        pending.intervention_request_id,
        InterventionResponse("marco", ActorKind.HUMAN, InterventionDecision.APPROVE, 1),
    )
    resumed = runtime.execute(
        Action(
            "deploy",
            {"version": "1.2.0"},
            requires_confirmation=True,
            intervention_id=pending.intervention_request_id,
        )
    )

    assert resumed.success
    assert resumed.status == ActionStatus.SUCCEEDED
    assert resumed.output == "deployed 1.2.0"


def test_approval_cannot_be_replayed_for_different_action_parameters() -> None:
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(),
    )
    runtime = ActionRuntime(
        {"deploy": lambda version: f"deployed {version}"},
        policy=ActionInterventionPolicy(service),
    )
    pending = runtime.execute(
        Action("deploy", {"version": "1.2.0"}, requires_confirmation=True)
    )
    assert pending.intervention_request_id is not None
    service.respond(
        pending.intervention_request_id,
        InterventionResponse("marco", ActorKind.HUMAN, InterventionDecision.APPROVE, 1),
    )

    replayed = runtime.execute(
        Action(
            "deploy",
            {"version": "2.0.0"},
            requires_confirmation=True,
            intervention_id=pending.intervention_request_id,
        )
    )

    assert replayed.status == ActionStatus.DENIED
    assert replayed.error == "intervention request does not match action"


def test_delegate_requires_active_explicit_delegation() -> None:
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(
            [
                Delegation(
                    "marco",
                    "release-agent",
                    "approve_action",
                    resource="package-index",
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            ]
        ),
    )
    request = service.request(
        Action(
            "publish",
            requires_confirmation=True,
            metadata={"resource": "package-index"},
        )
    )

    approved = service.respond(
        request.id,
        InterventionResponse(
            "release-agent", ActorKind.DELEGATE, InterventionDecision.APPROVE, 1
        ),
    )

    assert approved.response is not None
    assert approved.response.actor_id == "release-agent"
    untrusted_request = service.request(
        Action(
            "publish",
            requires_confirmation=True,
            metadata={"resource": "package-index"},
        )
    )
    with pytest.raises(PermissionError):
        service.respond(
            untrusted_request.id,
            InterventionResponse(
                "untrusted-agent", ActorKind.DELEGATE, InterventionDecision.APPROVE, 1
            ),
        )


def test_intervention_rejects_a_stale_response_version() -> None:
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(),
    )
    request = service.request(Action("deploy", requires_confirmation=True))

    with pytest.raises(ConcurrentWriteError):
        service.respond(
            request.id,
            InterventionResponse(
                "marco",
                ActorKind.HUMAN,
                InterventionDecision.APPROVE,
                request_version=2,
            ),
        )


def test_expired_intervention_cannot_authorize_an_action() -> None:
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(),
    )
    request = InterventionRequest(
        id="expired-request",
        kind=InterventionKind.APPROVAL,
        action_name="deploy",
        resource="*",
        summary="Deploy",
        created_at=datetime.now(UTC) - timedelta(minutes=2),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    service.store.create(request)

    record = service.get(request.id)

    assert record.status == InterventionStatus.EXPIRED


def test_non_approval_request_can_store_structured_human_input() -> None:
    store = InMemoryInterventionStore()
    request = InterventionRequest(
        id="input-request",
        kind=InterventionKind.INPUT,
        action_name="choose_region",
        resource="simulation",
        summary="Choose a containment region",
    )
    store.create(request)

    record = store.resolve(
        request.id,
        InterventionResponse(
            "marco",
            ActorKind.HUMAN,
            InterventionDecision.PROVIDE_INPUT,
            request_version=1,
            payload={"region": "coastal"},
        ),
        expected_version=1,
    )

    assert record.status == InterventionStatus.RESPONDED
    assert record.response is not None
    assert record.response.payload == {"region": "coastal"}


def test_intervention_preserves_action_provenance() -> None:
    provenance = Provenance.external("release-console")
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(),
    )

    request = service.request(
        Action("deploy", metadata={"provenance": provenance})
    )

    assert request.provenance == provenance
    assert request.provenance.trust == TrustLevel.EXTERNAL

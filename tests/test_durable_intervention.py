from dataclasses import replace

import pytest

from bbagents.intervention import (
    ActionInterventionService,
    ActorKind,
    HumanOrDelegatedAuthority,
    InterventionDecision,
    InterventionRequest,
    InterventionResponse,
    InterventionStatus,
    StateInterventionStore,
)
from bbagents.persistence import ConcurrentWriteError, SQLiteStateStore
from bbagents.runtime import Action


def test_saved_approval_can_be_revoked_after_restart(tmp_path):
    path = tmp_path / "approvals.db"
    service = ActionInterventionService(
        StateInterventionStore(SQLiteStateStore(path)), HumanOrDelegatedAuthority()
    )
    action = Action("publish", requires_confirmation=True)
    request = service.request(action)
    service.respond(
        request.id,
        InterventionResponse("human", ActorKind.HUMAN, InterventionDecision.APPROVE, 1),
    )
    store = StateInterventionStore(SQLiteStateStore(path))
    assert store.get(request.id).status == InterventionStatus.APPROVED
    store.revoke(request.id, expected_version=2)
    assert not service.decide(replace(action, intervention_id=request.id)).allowed
    with pytest.raises(ConcurrentWriteError):
        store.revoke(request.id, expected_version=2)


def test_expiry_also_invalidates_a_previously_approved_request(tmp_path, monkeypatch):
    store = StateInterventionStore(SQLiteStateStore(tmp_path / "approvals.db"))
    service = ActionInterventionService(store, HumanOrDelegatedAuthority())
    action = Action("publish", requires_confirmation=True)
    request = service.request(action)
    service.respond(
        request.id,
        InterventionResponse("human", ActorKind.HUMAN, InterventionDecision.APPROVE, 1),
    )
    monkeypatch.setattr(InterventionRequest, "is_expired", lambda self: True)
    assert not service.decide(replace(action, intervention_id=request.id)).allowed
    assert store.get(request.id).status == InterventionStatus.EXPIRED

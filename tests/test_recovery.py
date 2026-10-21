import asyncio
from pathlib import Path

import pytest

from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.persistence import SQLiteStateStore
from bbagents.recovery import (
    ExecutionJournal,
    ExecutionRecord,
    RecoverableActionRuntime,
    Reconciliation,
    ReconciliationStatus,
    action_fingerprint,
)
from bbagents.runtime import Action, ActionResult, ActionStatus, AuthorizationDecision


class Lookup:
    def __init__(self, external):
        self.external = external

    async def reconcile(self, action, record):
        if record.id in self.external:
            return Reconciliation(
                ReconciliationStatus.COMPLETED,
                ActionResult(ActionStatus.SUCCEEDED, self.external[record.id]),
            )
        return Reconciliation(ReconciliationStatus.UNKNOWN)


def test_recover_after_effect_before_result_was_recorded(tmp_path: Path) -> None:
    action = Action("publish", {"version": "1"}, idempotency_key="release-1")
    journal = ExecutionJournal(SQLiteStateStore(tmp_path / "journal.db"))
    journal.save(
        ExecutionRecord("release", action_fingerprint(action), "running", 0),
        expected_version=0,
    )
    external = {"release": "published"}
    calls = []

    async def publish(action, context):
        calls.append(True)

    runtime = RecoverableActionRuntime(
        AsyncActionRuntime(AsyncFunctionAdapter({"publish": publish})),
        journal,
        Lookup(external),
    )
    assert (
        asyncio.run(runtime.execute(action, execution_id="release")).output
        == "published"
    )
    assert asyncio.run(runtime.execute(action, execution_id="release")).success
    assert calls == []
    with pytest.raises(ValueError, match="different action"):
        asyncio.run(
            runtime.execute(Action("publish", {"version": "2"}), execution_id="release")
        )


def test_unknown_outcome_and_live_claim_never_repeat(tmp_path: Path) -> None:
    action = Action("publish")
    journal = ExecutionJournal(SQLiteStateStore(tmp_path / "journal.db"))
    journal.save(
        ExecutionRecord("release", action_fingerprint(action), "running", 100),
        expected_version=0,
    )
    runtime = RecoverableActionRuntime(
        AsyncActionRuntime(AsyncFunctionAdapter({})),
        journal,
        Lookup({}),
        clock=lambda: 0,
    )
    assert asyncio.run(runtime.execute(action, execution_id="release")).outcome_unknown
    runtime.clock = lambda: 200
    assert asyncio.run(runtime.execute(action, execution_id="release")).outcome_unknown
    assert journal.load("release")[1] == 1


def test_replay_rechecks_current_authority(tmp_path: Path) -> None:
    class Policy:
        allowed = True

        def decide(self, action):
            return (
                AuthorizationDecision.allow("yes")
                if self.allowed
                else AuthorizationDecision.deny("revoked")
            )

    async def publish(action, context):
        return "done"

    policy = Policy()
    runtime = RecoverableActionRuntime(
        AsyncActionRuntime(AsyncFunctionAdapter({"publish": publish}), policy=policy),
        ExecutionJournal(SQLiteStateStore(tmp_path / "journal.db")),
        Lookup({}),
    )
    action = Action("publish")
    assert asyncio.run(runtime.execute(action, execution_id="one")).success
    policy.allowed = False
    assert (
        asyncio.run(runtime.execute(action, execution_id="one")).status
        == ActionStatus.DENIED
    )

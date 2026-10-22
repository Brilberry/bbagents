"""Former README example: locally approved, recoverable simulated publication."""

import asyncio
from dataclasses import replace
from pathlib import Path

from bbagents import Action, ActionResult, ActionStatus
from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.intervention import (
    ActionInterventionPolicy, ActionInterventionService, ActorKind,
    HumanOrDelegatedAuthority, InterventionDecision, InterventionResponse,
    InterventionStatus, StateInterventionStore,
)
from bbagents.persistence import SQLiteStateStore
from bbagents.recovery import (
    ExecutionJournal, RecoverableActionRuntime, Reconciliation, ReconciliationStatus,
)

directory = Path(".release-example")
directory.mkdir(exist_ok=True)
store = SQLiteStateStore(directory / "state.db")
approvals = ActionInterventionService(
    StateInterventionStore(store), HumanOrDelegatedAuthority(),
)
artifact = directory / "bbagents-0.1.0.published"
receipt = "bbagents 0.1.0 published to the simulated index"


async def publish(action, context):
    # Exclusive creation makes this simulated publication immutable.
    with artifact.open("x") as file:
        file.write(receipt)
    return receipt


class PackageIndexLookup:
    async def reconcile(self, action, record):
        if artifact.exists() and artifact.read_text() == receipt:
            return Reconciliation(
                ReconciliationStatus.COMPLETED,
                ActionResult(ActionStatus.SUCCEEDED, receipt),
            )
        # Absence alone does not prove a previous attempt cannot still finish.
        return Reconciliation(ReconciliationStatus.UNKNOWN)


async def main():
    action = Action(
        "publish", {"package": "bbagents", "version": "0.1.0"},
        expected_effect="Publish bbagents 0.1.0 to the simulated index",
        requires_confirmation=True, idempotency_key="bbagents-0.1.0",
        metadata={"resource": "simulated-index"},
    )
    saved = store.load("release-approval")
    if saved is None:
        request = approvals.request(action)
        store.save("release-approval", {"id": request.id}, expected_version=0)
        print("Awaiting human approval. Run again to review.")
        return

    record = approvals.get(saved.value["id"])
    if record.status == InterventionStatus.PENDING:
        if input(f"{record.request.summary}? [yes/no] ").strip().lower() != "yes":
            print("Not approved; nothing published.")
            return
        # This CLI trusts its local operator, not model-supplied identity claims.
        approvals.respond(record.request.id, InterventionResponse(
            "local-operator", ActorKind.HUMAN, InterventionDecision.APPROVE,
            record.version,
        ))

    runtime = RecoverableActionRuntime(
        AsyncActionRuntime(
            AsyncFunctionAdapter({"publish": publish}),
            policy=ActionInterventionPolicy(approvals),
        ),
        ExecutionJournal(store), PackageIndexLookup(),
    )
    result = await runtime.execute(
        replace(action, intervention_id=record.request.id),
        execution_id="publish-bbagents-0.1.0",
    )
    print(f"Release result: {result.output if result.success else result.error}")


asyncio.run(main())

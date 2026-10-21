"""Fictional game fixture exercising shared governance, recovery and task contracts.

This is a software integration fixture, not a disease model or health guidance.
The application owns its event flow; no notion of rounds is introduced.
"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.intervention import (
    ActionInterventionPolicy,
    ActionInterventionService,
    ActorKind,
    HumanOrDelegatedAuthority,
    InterventionDecision,
    InterventionResponse,
    StateInterventionStore,
)
from bbagents.lifecycle import (
    AcceptanceCriterion,
    TaskLifecycle,
    TaskRecord,
    TaskRepository,
)
from bbagents.persistence import SQLiteStateStore
from bbagents.recovery import (
    ExecutionJournal,
    RecoverableActionRuntime,
    Reconciliation,
    ReconciliationStatus,
)
from bbagents.runtime import Action, ActionResult, ActionStatus


class PandemicResponseFixture:
    def __init__(self, directory: Path) -> None:
        self.effects = SQLiteStateStore[bool](directory / "simulation.db")
        self.interventions = ActionInterventionService(
            StateInterventionStore(SQLiteStateStore(directory / "approvals.db")),
            HumanOrDelegatedAuthority(),
        )
        self.tasks = TaskRepository(SQLiteStateStore(directory / "tasks.db"))

        async def allocate(action, context):
            self.effects.save("response-active", True, expected_version=0)
            return "response-active"

        self.runtime = RecoverableActionRuntime(
            AsyncActionRuntime(
                AsyncFunctionAdapter({"allocate_response": allocate}),
                policy=ActionInterventionPolicy(self.interventions),
            ),
            ExecutionJournal(SQLiteStateStore(directory / "execution.db")),
            self,
        )

    async def reconcile(self, action, record):
        if self.effects.load("response-active") is not None:
            return Reconciliation(
                ReconciliationStatus.COMPLETED,
                ActionResult(ActionStatus.SUCCEEDED, "response-active"),
            )
        return Reconciliation(ReconciliationStatus.NOT_EXECUTED)

    def respond(self, intervention_id: str | None = None) -> ActionResult:
        action = Action(
            "allocate_response",
            expected_effect="Activate a fictional response resource",
            requires_confirmation=True,
            intervention_id=intervention_id,
            metadata={"resource": "simulation:region-a"},
            idempotency_key="scenario-1",
        )
        result = asyncio.run(self.runtime.execute(action, execution_id="scenario-1"))
        if result.success:
            task = TaskRecord(
                "scenario-1",
                "Activate response",
                (
                    AcceptanceCriterion(
                        "active",
                        "Resource active in game state",
                        True,
                        ("simulation:response-active",),
                    ),
                ),
            )
            previous = self.tasks.load(task.id)
            if previous is None:
                version = self.tasks.save(task, expected_version=0)
                asyncio.run(
                    TaskLifecycle(self.tasks).complete(task, expected_version=version)
                )
        return result


def run_example() -> None:
    with TemporaryDirectory() as directory:
        fixture = PandemicResponseFixture(Path(directory))
        pending = fixture.respond()
        assert pending.status == ActionStatus.PENDING_INTERVENTION
        fixture.interventions.respond(
            pending.intervention_request_id,
            InterventionResponse(
                "player",
                ActorKind.HUMAN,
                InterventionDecision.APPROVE,
                1,
            ),
        )
        restarted = PandemicResponseFixture(Path(directory))
        assert restarted.respond(pending.intervention_request_id).success
        assert restarted.respond(pending.intervention_request_id).success
        print(
            "Approved response restored; repeated invocation reused confirmed result."
        )


if __name__ == "__main__":
    run_example()

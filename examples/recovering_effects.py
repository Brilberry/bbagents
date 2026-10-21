"""Recover an external success whose result was lost before checkpointing."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

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
from bbagents.runtime import Action, ActionResult, ActionStatus


class ExternalLookup:
    async def reconcile(self, action, record):
        return Reconciliation(
            ReconciliationStatus.COMPLETED,
            ActionResult(ActionStatus.SUCCEEDED, "confirmed externally"),
        )


def run_example() -> None:
    with TemporaryDirectory() as directory:
        journal = ExecutionJournal(SQLiteStateStore(Path(directory) / "journal.db"))
        action = Action("publish", idempotency_key="release-1")
        journal.save(
            ExecutionRecord(
                "release-1",
                action_fingerprint(action),
                "running",
                0,
            ),
            expected_version=0,
        )
        runtime = RecoverableActionRuntime(
            AsyncActionRuntime(AsyncFunctionAdapter({})), journal, ExternalLookup()
        )
        result = asyncio.run(runtime.execute(action, execution_id="release-1"))
        assert result.success
        print(result.output, "without executing again")


if __name__ == "__main__":
    run_example()

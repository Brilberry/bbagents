import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.hooks import Hook, HookBoundary, HookDecision, HookRunner
from bbagents.lifecycle import (
    AcceptanceCriterion,
    ExecutionBudget,
    TaskLifecycle,
    TaskRecord,
    TaskRepository,
)
from bbagents.observability import InMemoryTrace
from bbagents.persistence import SQLiteStateStore
from bbagents.runtime import Action, ActionStatus


def test_restart_completion_evidence_and_budget(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    repository = TaskRepository(SQLiteStateStore(path))
    task = TaskRecord("one", "Release", (AcceptanceCriterion("tests", "Pass tests"),))
    repository.save(task, expected_version=0)
    lifecycle = TaskLifecycle(repository)
    with pytest.raises(ValueError, match="evidence"):
        asyncio.run(lifecycle.complete(task, expected_version=1))
    loaded, version = TaskRepository(SQLiteStateStore(path)).load("one")
    verified = replace(
        loaded,
        criteria=(
            AcceptanceCriterion("tests", "Pass tests", True, ("test-report:1",)),
        ),
    )
    assert (
        asyncio.run(lifecycle.complete(verified, expected_version=version)).status
        == "complete"
    )


def test_no_progress_exhaustion_is_persisted(tmp_path: Path) -> None:
    repository = TaskRepository(SQLiteStateStore(tmp_path / "tasks.db"))
    task = TaskRecord("one", "Goal", ())
    repository.save(task, expected_version=0)
    lifecycle = TaskLifecycle(repository, budget=ExecutionBudget(max_no_progress=1))
    result = lifecycle.record_attempt(
        task, expected_version=1, made_progress=False, elapsed_seconds=1
    )
    assert result.status == "exhausted"
    assert repository.load("one")[0].status == "exhausted"
    with pytest.raises(ValueError, match="another attempt"):
        lifecycle.record_attempt(
            result, expected_version=2, made_progress=True, elapsed_seconds=1
        )


def test_blocking_hook_timeout_denies_dispatch_and_traces_failure() -> None:
    async def stuck(context):
        await asyncio.sleep(10)
        return HookDecision(True)

    effects = []

    async def handler(action, context):
        effects.append(1)

    trace = InMemoryTrace()
    runtime = AsyncActionRuntime(
        AsyncFunctionAdapter({"effect": handler}),
        hooks=HookRunner(
            (Hook("approval", HookBoundary.BEFORE_ACTION, stuck, 0.01),), trace=trace
        ),
    )
    result = asyncio.run(runtime.execute(Action("effect")))
    assert result.status == ActionStatus.DENIED and not result.outcome_unknown
    assert effects == [] and trace.events_named("hook.failed")
    with pytest.raises(ValueError, match="observational"):
        Hook("unsafe", HookBoundary.BEFORE_ACTION, stuck, blocking=False)


@pytest.mark.parametrize(
    "limits,elapsed,cost",
    [
        ({"max_attempts": 1}, 0, 0),
        ({"max_elapsed_seconds": 1}, 1, 0),
        ({"max_cost": 1}, 0, 1),
    ],
)
def test_each_budget_stops_further_attempts(tmp_path, limits, elapsed, cost):
    repository = TaskRepository(SQLiteStateStore(tmp_path / "tasks.db"))
    task = TaskRecord("one", "Goal", ())
    repository.save(task, expected_version=0)
    lifecycle = TaskLifecycle(repository, budget=ExecutionBudget(**limits))
    updated = lifecycle.record_attempt(
        task,
        expected_version=1,
        made_progress=True,
        elapsed_seconds=elapsed,
        cost=cost,
    )
    assert repository.load("one")[0].status == "exhausted"
    with pytest.raises(ValueError):
        lifecycle.record_attempt(
            updated, expected_version=2, made_progress=True, elapsed_seconds=0
        )

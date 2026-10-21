"""Durable completion depends on evidence, not a model saying 'done'."""

import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from bbagents.lifecycle import (
    AcceptanceCriterion,
    TaskLifecycle,
    TaskRecord,
    TaskRepository,
)
from bbagents.persistence import SQLiteStateStore


def run_example() -> None:
    with TemporaryDirectory() as directory:
        repository = TaskRepository(SQLiteStateStore(Path(directory) / "tasks.db"))
        task = TaskRecord(
            "release", "Verify release", (AcceptanceCriterion("tests", "Pass tests"),)
        )
        repository.save(task, expected_version=0)
        task = replace(
            task,
            criteria=(AcceptanceCriterion("tests", "Pass tests", True, ("report:1",)),),
        )
        result = asyncio.run(
            TaskLifecycle(repository).complete(task, expected_version=1)
        )
        print(result.status, result.criteria[0].evidence_refs)


if __name__ == "__main__":
    run_example()

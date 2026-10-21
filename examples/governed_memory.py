"""Scope isolation and rollback share the same contracts across applications."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from bbagents.governed_memory import ScopedMemoryStore
from bbagents.memory import MemoryRecord
from bbagents.persistence import SQLiteStateStore
from bbagents.revisions import AccessContext, RevisionRepository


class OwnerPolicy:
    def permits(self, context, operation):
        return context.subject_id == "owner"


def run_example() -> None:
    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(Path(directory) / "memory.db")
        repo = RevisionRepository(
            store,
            AccessContext("owner", "project-a"),
            OwnerPolicy(),
            namespace="memory",
        )
        memory = ScopedMemoryStore(repo)
        first = memory.remember(MemoryRecord("Run unit tests", id="release-check"))
        memory.remember(
            replace(first, content="Run unit and integration tests"), expected_version=1
        )
        repo.activate(
            first.id, 1, expected_active=2, reason="Revert unreviewed expansion"
        )
        assert memory.get(first.id).content == "Run unit tests"
        other = ScopedMemoryStore(
            RevisionRepository(
                store,
                AccessContext("owner", "project-b"),
                OwnerPolicy(),
                namespace="memory",
            )
        )
        assert other.retrieve("tests") == []
        print("Scope isolated; rollback preserved both revisions.")


if __name__ == "__main__":
    run_example()

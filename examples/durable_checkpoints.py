"""Compare-and-swap checkpoints survive reopening the application."""

from pathlib import Path
from tempfile import TemporaryDirectory

from bbagents.persistence import ConcurrentWriteError, SQLiteStateStore


def run_example() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "checkpoints.db"
        store: SQLiteStateStore[dict[str, str]] = SQLiteStateStore(path)
        first = store.save("task", {"status": "prepared"}, expected_version=0)
        reopened: SQLiteStateStore[dict[str, str]] = SQLiteStateStore(path)
        assert reopened.load("task") == first
        reopened.save("task", {"status": "verified"}, expected_version=first.version)
        try:
            store.save("task", {"status": "stale"}, expected_version=first.version)
        except ConcurrentWriteError:
            print("Stale update rejected; verified checkpoint preserved.")


if __name__ == "__main__":
    run_example()

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from bbagents.persistence import ConcurrentWriteError, FileStateStore, SQLiteStateStore


def test_sqlite_competing_writers_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store: SQLiteStateStore[dict[str, int]] = SQLiteStateStore(path)
    first = store.save("key", {"n": 0}, expected_version=0)
    barrier = Barrier(2)

    def update(n: int) -> bool:
        other: SQLiteStateStore[dict[str, int]] = SQLiteStateStore(path)
        barrier.wait()
        try:
            other.save("key", {"n": n}, expected_version=first.version)
        except ConcurrentWriteError:
            return False
        return True

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(update, [1, 2])) == [False, True]
    restored = SQLiteStateStore[dict[str, int]](path).load("key")
    assert restored is not None and restored.version == 2


@pytest.mark.parametrize("store_class", [FileStateStore, SQLiteStateStore])
def test_failed_serialization_keeps_previous_checkpoint(
    tmp_path: Path, store_class
) -> None:
    store = store_class(tmp_path / "state")
    first = store.save("key", "good")
    with pytest.raises(TypeError):
        store.save("key", object(), expected_version=1)
    assert store.load("key") == first
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_replace_cleans_temporary_file(tmp_path: Path, monkeypatch) -> None:
    store: FileStateStore[str] = FileStateStore(tmp_path)
    first = store.save("key", "good")

    def fail(*args):
        raise OSError("interrupted replace")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        store.save("key", "new")
    assert store.load("key") == first
    assert not list(tmp_path.glob("*.tmp"))

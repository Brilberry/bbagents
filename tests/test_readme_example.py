"""Exercise the release example and the island guide's runnable snippet."""

import os
from pathlib import Path
import subprocess
import sys

from bbagents.persistence import SQLiteStateStore
from bbagents.recovery import ExecutionJournal, ExecutionRecord


def run_readme(directory, answer=""):
    root = Path(__file__).resolve().parents[1]
    code = (root / "examples/recoverable_release.py").read_text()
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=directory, env=env,
        input=answer, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_island_overview_snippet():
    root = Path(__file__).resolve().parents[1]
    guide = root / "examples/survival_island/OVERVIEW.md"
    code = guide.read_text().split("```python\n", 1)[1].split("```", 1)[0]
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    # Predator-enabled runs may kill survivors; verify the snippet's result shape.
    from ast import literal_eval
    survivors = literal_eval(result.stdout.strip())
    assert [name for name, _ in survivors] == ["Ari", "Bo", "Cyra", "Dax", "Ena"]
    assert all(isinstance(alive, bool) for _, alive in survivors)


def approve_release(directory):
    assert "Awaiting human approval" in run_readme(directory)
    assert "simulated index" in run_readme(directory, "yes\n")


def test_readme_pauses_defers_approves_and_reuses_result(tmp_path):
    artifact = tmp_path / ".release-example/bbagents-0.1.0.published"
    assert "Awaiting human approval" in run_readme(tmp_path)
    assert not artifact.exists()
    assert "Not approved; nothing published" in run_readme(tmp_path, "no\n")
    assert not artifact.exists()
    assert "simulated index" in run_readme(tmp_path, "yes\n")
    before = artifact.stat()
    assert "simulated index" in run_readme(tmp_path)
    after = artifact.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def mark_uncertain(directory):
    journal = ExecutionJournal(
        SQLiteStateStore(directory / ".release-example/state.db")
    )
    record, version = journal.load("publish-bbagents-0.1.0")
    journal.save(
        ExecutionRecord(record.id, record.fingerprint, "uncertain", 0),
        expected_version=version,
    )
    return journal


def test_readme_reconciles_publication_without_repeating_it(tmp_path):
    approve_release(tmp_path)
    journal = mark_uncertain(tmp_path)
    artifact = tmp_path / ".release-example/bbagents-0.1.0.published"
    before = artifact.stat().st_mtime_ns
    assert "simulated index" in run_readme(tmp_path)
    assert artifact.stat().st_mtime_ns == before
    record, _ = journal.load("publish-bbagents-0.1.0")
    assert record.state == "finished"


def test_readme_does_not_replay_an_unknown_outcome(tmp_path):
    approve_release(tmp_path)
    mark_uncertain(tmp_path)
    artifact = tmp_path / ".release-example/bbagents-0.1.0.published"
    artifact.unlink()
    assert "External outcome remains unknown" in run_readme(tmp_path)
    assert not artifact.exists()

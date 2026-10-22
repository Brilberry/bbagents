"""Run every offline example as a user would; hosted exporters use adapter fakes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(
    path
    for path in (ROOT / "examples").glob("*.py")
    if path.name not in {"__init__.py", "langsmith_tracing.py"}
)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.stem)
def test_offline_example(script: Path, tmp_path: Path) -> None:
    if script.stem.startswith("langgraph"):
        pytest.importorskip("langgraph")
    if script.stem.startswith("langchain"):
        pytest.importorskip("langchain_core")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    for key in tuple(env):
        if key.startswith(("LANGFUSE_", "LANGSMITH_", "LANGCHAIN_")):
            env.pop(key)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path if script.stem == "recoverable_release" else ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

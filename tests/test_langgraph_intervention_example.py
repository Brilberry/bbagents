import pytest

pytest.importorskip("langgraph")

from examples.langgraph_intervention import run_example


def test_langgraph_interrupt_resolves_and_resumes_intervention() -> None:
    state, published_versions = run_example()

    assert state["status"] == "released"
    assert state["publication"] == "package-index"
    assert published_versions == ["0.1.0"]

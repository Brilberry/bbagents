import pytest

pytest.importorskip("langgraph")

from examples.langgraph_integration import deserialize_agent, run_example

from bbagents.planning import StepStatus


def test_langgraph_orchestrates_bbagents_plan_and_tools() -> None:
    state, trace, checkpoint_count = run_example()
    agent = deserialize_agent(state["agent"])

    assert agent.completed
    assert all(step.status == StepStatus.DONE for step in agent.plan.steps)
    assert "AuthMiddleware" in state["strategy"]
    assert "SessionStore" in state["strategy"]
    assert len(trace.events) == 6
    assert checkpoint_count >= 4

from __future__ import annotations

from typing import Any

from bbagents.integrations.langfuse import LangfuseTrace


class FakeObservation:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.children: list[FakeObservation] = []
        self.update_kwargs: dict[str, Any] | None = None
        self.ended = False

    def start_observation(self, **kwargs: Any) -> "FakeObservation":
        child = FakeObservation(**kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs: Any) -> None:
        self.update_kwargs = kwargs

    def end(self) -> None:
        self.ended = True


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.roots: list[FakeObservation] = []
        self.flushed = False

    def start_observation(self, **kwargs: Any) -> FakeObservation:
        observation = FakeObservation(**kwargs)
        self.roots.append(observation)
        return observation

    def flush(self) -> None:
        self.flushed = True


def test_langfuse_trace_creates_nested_agent_step_and_tool_observations() -> None:
    client = FakeLangfuseClient()
    trace = LangfuseTrace(client=client)
    trace.record("agent.run.started", {"goal": "inspect"})
    trace.record("step.started", {"step_id": "inspect", "title": "Inspect repo"})
    trace.record(
        "tool.started",
        {
            "step_id": "inspect",
            "tool_name": "read_file",
            "arguments": {"path": "README.md"},
        },
    )
    trace.record(
        "tool.finished",
        {
            "step_id": "inspect",
            "tool_name": "read_file",
            "status": "completed",
            "observation_source": "repository",
        },
    )
    trace.record(
        "step.finished",
        {"step_id": "inspect", "status": "done", "error": None},
    )
    trace.record("agent.run.finished", {"completed": True, "reason": "plan_complete"})
    trace.flush()

    root = client.roots[0]
    step = root.children[0]
    tool = step.children[0]
    assert root.kwargs["name"] == "bbagents.agent.run"
    assert step.kwargs["name"] == "Inspect repo"
    assert tool.kwargs["name"] == "read_file"
    assert tool.update_kwargs == {
        "output": {
            "step_id": "inspect",
            "tool_name": "read_file",
            "status": "completed",
            "observation_source": "repository",
        }
    }
    assert root.ended and step.ended and tool.ended and client.flushed


def test_langfuse_trace_marks_failed_observations() -> None:
    client = FakeLangfuseClient()
    trace = LangfuseTrace(client=client)
    trace.record("agent.run.started", {"goal": "inspect"})
    trace.record("step.started", {"step_id": "inspect", "title": "Inspect repo"})
    trace.record(
        "tool.started",
        {"step_id": "inspect", "tool_name": "read_file", "arguments": {}},
    )
    trace.record(
        "tool.finished",
        {
            "step_id": "inspect",
            "tool_name": "read_file",
            "status": "failed",
            "error": "permission denied",
        },
    )

    assert client.roots[0].children[0].children[0].update_kwargs == {
        "output": {
            "step_id": "inspect",
            "tool_name": "read_file",
            "status": "failed",
            "error": "permission denied",
        },
        "level": "ERROR",
        "status_message": "permission denied",
    }

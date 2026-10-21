from __future__ import annotations

from typing import Any

from bbagents.integrations.langsmith import LangSmithTrace


class FakeRunTree:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.children: list[FakeRunTree] = []
        self.posted = False
        self.outputs: dict[str, Any] | None = None
        self.error: str | None = None
        self.patched = False

    def create_child(self, **kwargs: Any) -> "FakeRunTree":
        child = FakeRunTree(**kwargs)
        self.children.append(child)
        return child

    def post(self) -> None:
        self.posted = True

    def end(self, *, outputs: dict[str, Any], error: str | None = None) -> None:
        self.outputs = outputs
        self.error = error

    def patch(self) -> None:
        self.patched = True


def test_langsmith_trace_creates_nested_agent_step_and_tool_runs() -> None:
    roots: list[FakeRunTree] = []

    def factory(**kwargs: Any) -> FakeRunTree:
        root = FakeRunTree(**kwargs)
        roots.append(root)
        return root

    trace = LangSmithTrace(project_name="test-project", run_tree_factory=factory)
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

    root = roots[0]
    step = root.children[0]
    tool = step.children[0]
    assert root.kwargs["name"] == "bbagents.agent.run"
    assert root.kwargs["project_name"] == "test-project"
    assert step.kwargs["run_type"] == "chain"
    assert tool.kwargs["run_type"] == "tool"
    assert tool.outputs == {
        "step_id": "inspect",
        "tool_name": "read_file",
        "status": "completed",
        "observation_source": "repository",
    }
    assert root.outputs == {"completed": True, "reason": "plan_complete"}
    assert all(
        (
            root.posted,
            step.posted,
            tool.posted,
            root.patched,
            step.patched,
            tool.patched,
        )
    )


def test_langsmith_trace_marks_failed_tool_spans_as_errors() -> None:
    roots: list[FakeRunTree] = []

    def factory(**kwargs: Any) -> FakeRunTree:
        root = FakeRunTree(**kwargs)
        roots.append(root)
        return root

    trace = LangSmithTrace(run_tree_factory=factory)
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

    assert roots[0].children[0].children[0].error == "permission denied"

"""Optional LangSmith adapter for structured ``bbagents`` execution events.

Install the ``langsmith`` extra and configure ``LANGSMITH_API_KEY`` before using
this adapter. The adapter translates the planning runtime's agent, step, and tool
events into a single nested LangSmith trace.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from ..observability import TraceEvent, TraceEventName


class LangSmithTrace:
    """Publish a planning run as a nested LangSmith trace.

    ``run_tree_factory`` exists for application testing and custom LangSmith
    deployments. Ordinary applications should omit it and allow the adapter to
    construct LangSmith ``RunTree`` instances directly.
    """

    def __init__(
        self,
        *,
        project_name: str = "bbagents",
        client: Any | None = None,
        run_tree_factory: Callable[..., Any] | None = None,
    ) -> None:
        if run_tree_factory is None:
            try:
                from langsmith.run_trees import RunTree
            except ImportError as exc:
                raise ImportError(
                    "LangSmith tracing requires the optional dependency. "
                    "Install it with `pip install bbagents[langsmith]`."
                ) from exc
            run_tree_factory = RunTree

        self._project_name = project_name
        self._client = client
        self._run_tree_factory = run_tree_factory
        self._root: Any | None = None
        self._steps: dict[str, Any] = {}
        self._tools: dict[tuple[str, str], Any] = {}

    def record(
        self,
        event: TraceEventName | str | TraceEvent,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Translate a structured runtime event into a LangSmith run update."""

        event, payload = _event_parts(event, payload)
        if event == "agent.run.started":
            self._root = self._new_root(payload)
        elif event == "agent.run.finished":
            self._finish(self._ensure_root(), payload)
        elif event == "step.started":
            step_id = str(payload["step_id"])
            step = self._ensure_root().create_child(
                name=str(payload["title"]), run_type="chain", inputs=payload
            )
            step.post()
            self._steps[step_id] = step
        elif event == "step.finished":
            self._finish(self._steps.pop(str(payload["step_id"])), payload)
        elif event == "tool.started":
            step_id = str(payload["step_id"])
            tool_name = str(payload["tool_name"])
            tool = self._steps[step_id].create_child(
                name=tool_name,
                run_type="tool",
                inputs={"arguments": payload["arguments"]},
            )
            tool.post()
            self._tools[(step_id, tool_name)] = tool
        elif event == "tool.finished":
            key = (str(payload["step_id"]), str(payload["tool_name"]))
            self._finish(self._tools.pop(key), payload)

    def _new_root(self, inputs: Mapping[str, Any]) -> Any:
        root = self._run_tree_factory(
            name="bbagents.agent.run",
            run_type="chain",
            inputs=dict(inputs),
            project_name=self._project_name,
            ls_client=self._client,
        )
        root.post()
        return root

    def _ensure_root(self) -> Any:
        if self._root is None:
            self._root = self._new_root({})
        return self._root

    @staticmethod
    def _finish(run: Any, outputs: Mapping[str, Any]) -> None:
        error = outputs.get("error")
        run.end(outputs=outputs, error=str(error) if error is not None else None)
        run.patch()


def _event_parts(
    event: TraceEventName | str | TraceEvent,
    payload: Mapping[str, Any] | None,
) -> tuple[str, Mapping[str, Any]]:
    if isinstance(event, TraceEvent):
        name = (
            event.name.value
            if isinstance(event.name, TraceEventName)
            else event.name
        )
        return name, event.attributes
    return (event.value if isinstance(event, TraceEventName) else event), payload or {}

"""Optional Langfuse adapter for structured ``bbagents`` execution events."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any, Mapping

from ..observability import TraceEvent, TraceEventName


class LangfuseTrace:
    """Publish a planning run as nested Langfuse observations.

    The adapter uses Langfuse's OpenTelemetry-based observations API. Supplying a
    client is useful for self-hosted deployments and tests; otherwise Langfuse
    reads its standard environment configuration.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        if client is None:
            if client_factory is None:
                try:
                    get_client = getattr(import_module("langfuse"), "get_client")
                except ImportError as exc:
                    raise ImportError(
                        "Langfuse tracing requires the optional dependency. "
                        "Install it with `pip install bbagents[langfuse]`."
                    ) from exc
                client_factory = get_client
            client = client_factory()

        self._client = client
        self._root: Any | None = None
        self._steps: dict[str, Any] = {}
        self._tools: dict[tuple[str, str], Any] = {}

    def record(
        self,
        event: TraceEventName | str | TraceEvent,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Translate planning events into a nested Langfuse observation tree."""

        event, payload = _event_parts(event, payload)
        if event == "agent.run.started":
            self._root = self._start_root(payload)
        elif event == "agent.run.finished":
            self._finish(self._ensure_root(), payload)
        elif event == "step.started":
            step_id = str(payload["step_id"])
            step = self._ensure_root().start_observation(
                name=str(payload["title"]), input=payload
            )
            self._steps[step_id] = step
        elif event == "step.finished":
            self._finish(self._steps.pop(str(payload["step_id"])), payload)
        elif event == "tool.started":
            step_id = str(payload["step_id"])
            tool_name = str(payload["tool_name"])
            tool = self._steps[step_id].start_observation(
                name=tool_name,
                input={"arguments": payload["arguments"]},
            )
            self._tools[(step_id, tool_name)] = tool
        elif event == "tool.finished":
            key = (str(payload["step_id"]), str(payload["tool_name"]))
            self._finish(self._tools.pop(key), payload)

    def flush(self) -> None:
        """Submit queued observations before a short-lived process exits."""

        flush = getattr(self._client, "flush", None)
        if callable(flush):
            flush()

    def _start_root(self, inputs: Mapping[str, Any]) -> Any:
        return self._client.start_observation(name="bbagents.agent.run", input=inputs)

    def _ensure_root(self) -> Any:
        if self._root is None:
            self._root = self._start_root({})
        return self._root

    @staticmethod
    def _finish(observation: Any, output: Mapping[str, Any]) -> None:
        error = output.get("error")
        if error is None:
            observation.update(output=output)
        else:
            observation.update(
                output=output,
                level="ERROR",
                status_message=str(error),
            )
        observation.end()


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

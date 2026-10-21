"""Deterministic tool registration and invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .core import Observation


class Tool(Protocol):
    name: str
    description: str

    def run(self, **kwargs: Any) -> Observation: ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FunctionTool:
    """Expose a Python callable through the Tool protocol."""

    name: str
    description: str
    function: Callable[..., Observation | str]

    def run(self, **kwargs: Any) -> Observation:
        result = self.function(**kwargs)
        if isinstance(result, Observation):
            return result
        return Observation(source=self.name, content=str(result))


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def execute(self, call: ToolCall) -> Observation:
        return self.get(call.tool_name).run(**dict(call.arguments))

    def descriptions(self) -> tuple[tuple[str, str], ...]:
        return tuple((tool.name, tool.description) for tool in self._tools.values())


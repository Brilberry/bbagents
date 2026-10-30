"""Offline decision-policy compilation, hysteresis, and emergency interruption."""

from dataclasses import dataclass
from typing import Any, Mapping

from bbagents import FunctionTool, ToolCall, ToolRegistry
from bbagents.decision_policies import DecisionCompiler
from bbagents.observability import InMemoryTrace
from bbagents.policy_registry import (
    ActionSpec,
    AttributeSpec,
    EnergyCost,
    EventSpec,
    Frame,
    ParameterSpec,
    PolicyRegistry,
    Scalar,
    ValueSpec,
)
from bbagents.policy_scheduler import PolicyScheduler
from bbagents.behavior_trees import Status


@dataclass(frozen=True)
class Context:
    hunger: int
    danger: bool = False


class IntervalAction:
    def __init__(self, tools: ToolRegistry, tool: str) -> None:
        self.tools, self.tool = tools, tool

    def tick(
        self,
        frame: Frame[Context],
        parameters: Mapping[str, Scalar],
        memory: dict[str, Any],
    ) -> Status:
        # Illustrative tools only record local evidence. An effectful application
        # must use its existing authorized, actor-bound execution path here.
        self.tools.execute(ToolCall(self.tool))
        memory["intervals"] = memory.get("intervals", 0) + 1
        return Status.RUNNING if memory["intervals"] < 3 else Status.SUCCESS

    def cancel(
        self,
        frame: Frame[Context],
        parameters: Mapping[str, Scalar],
        memory: dict[str, Any],
    ) -> None:
        # Stop outstanding work here; completed effects are not rolled back.
        pass


def run_example() -> dict[str, Any]:
    evidence: list[str] = []

    def record_food() -> str:
        evidence.append("food interval")
        return "Recorded food interval"

    def record_escape() -> str:
        evidence.append("escape interval")
        return "Recorded escape interval"

    tools = ToolRegistry(
        [
            FunctionTool("food", "Record an illustrative food interval", record_food),
            FunctionTool(
                "escape", "Record an illustrative escape interval", record_escape
            ),
        ]
    )
    registry = PolicyRegistry[Context](
        version="example-1",
        events=[
            EventSpec(
                "hungry",
                "Hungry",
                "level",
                fields=(
                    ParameterSpec("start", ValueSpec("number", 0, 100), False, 70),
                    ParameterSpec("stop", ValueSpec("number", 0, 100), False, 30),
                ),
                active=lambda frame: (
                    frame.context.hunger >= frame.event.payload["start"]
                ),
                release=lambda frame: (
                    frame.context.hunger <= frame.event.payload["stop"]
                ),
            ),
            EventSpec(
                "danger",
                "Danger nearby",
                "level",
                active=lambda frame: frame.context.danger,
            ),
        ],
        attributes=[
            AttributeSpec(
                "hunger",
                ValueSpec("number", 0, 100),
                lambda frame: frame.context.hunger,
            )
        ],
        actions=[
            ActionSpec(
                "food",
                "Find food",
                IntervalAction(tools, "food"),
                energy=EnergyCost(2, "per_interval"),
            ),
            ActionSpec(
                "escape",
                "Escape",
                IntervalAction(tools, "escape"),
                energy=EnergyCost(4, "per_interval"),
            ),
        ],
    )
    trace = InMemoryTrace()
    compiler = DecisionCompiler(registry, trace=trace)
    food = compiler.compile(
        {
            "version": 1,
            "id": "food",
            "event": "hungry",
            "priority": 60,
            "event_parameters": {"start": 70, "stop": 30},
            "root": {"id": "eat", "type": "action", "action": "food"},
        }
    )
    escape = compiler.compile(
        {
            "version": 1,
            "id": "danger",
            "event": "danger",
            "priority": 100,
            "root": {"id": "flee", "type": "action", "action": "escape"},
        }
    )
    scheduler = PolicyScheduler(registry, [food, escape], trace=trace)
    selections = [
        scheduler.tick(Context(80), step=0).policy,
        scheduler.tick(Context(60, danger=True), step=1).policy,
    ]
    scheduler = PolicyScheduler.restore(
        registry, [food, escape], scheduler.snapshot(), trace=trace
    )
    selections.append(scheduler.tick(Context(50), step=2).policy)
    selections.append(scheduler.tick(Context(20), step=3).policy)
    return {
        "selected": selections,
        "evidence": evidence,
        "trace": [
            {"name": event.name, "attributes": dict(event.attributes)}
            for event in trace.events
        ],
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_example(), sort_keys=True))

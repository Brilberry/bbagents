"""Offline running-action, safe-branch and interruption example (no model)."""

from dataclasses import dataclass
from typing import Any

from bbagents import FunctionTool, ToolCall, ToolRegistry
from bbagents.behavior_trees import Action, BehaviorTree, Branch, Execution, Status
from bbagents.observability import InMemoryTrace


@dataclass(frozen=True)
class Context:
    danger: bool = False


class Travel:
    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    def tick(self, context: Context, memory: dict[str, Any]) -> Status:
        # This illustrative tool is harmless. Effectful applications use their
        # actor-bound ActionRuntime authorization path, not an unguarded tool.
        self.tools.execute(ToolCall("travel_interval"))
        # The application tool owns that interval's ordinary action cost.
        memory["distance"] = memory.get("distance", 0) + 1
        return Status.SUCCESS if memory["distance"] >= 3 else Status.RUNNING

    def cancel(self, context: Context, memory: dict[str, Any]) -> None:
        # Release an outstanding operation here. Existing world movement remains.
        pass


def run_example() -> dict[str, str]:
    trace = InMemoryTrace()
    tools = ToolRegistry(
        [
            FunctionTool(
                "travel_interval",
                "Record one illustrative interval",
                lambda: "Interval complete",
            )
        ]
    )
    actions = {"travel": Travel(tools)}
    conditions = {"danger": lambda context: context.danger}
    tree = BehaviorTree[Context](
        Branch(
            "decision",
            "danger",
            Action("escape", "travel"),
            Action("explore", "travel"),
        ),
        actions=actions,
        conditions=conditions,
        trace=trace,
    )
    actor = tree.start()
    first = actor.tick(Context())
    actor = Execution.restore(tree, actor.snapshot())
    # The application scheduler notices a new higher-priority danger event.
    actor.cancel(Context(danger=True))
    interrupted = actor.tick(Context(danger=True))
    actor.tick(Context(danger=True))
    completed = actor.tick(Context(danger=True))
    return {
        "first": first.value,
        "interrupted": interrupted.value,
        "completed": completed.value,
    }


if __name__ == "__main__":
    print(run_example())

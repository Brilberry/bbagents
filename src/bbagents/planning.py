"""Explicit plans, deterministic step execution, and a local reference runner."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

from .core import Observation, TraceSink
from .tools import ToolCall, ToolRegistry


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class DependencyCycleError(ValueError):
    """Raised when a plan contains a dependency cycle.

    ``steps`` is the closed cycle path, so the first and last IDs are the same.
    """

    def __init__(self, steps: Iterable[str]) -> None:
        self.steps = tuple(steps)
        super().__init__(f"Plan dependency cycle: {' -> '.join(self.steps)}")


@dataclass(slots=True)
class PlanStep:
    id: str
    title: str
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class Plan:
    goal: str
    steps: list[PlanStep]
    version: int = 1

    def __post_init__(self) -> None:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan step IDs must be unique")
        known = set(ids)
        unknown = {dep for step in self.steps for dep in step.depends_on} - known
        if unknown:
            raise ValueError(f"Unknown plan dependencies: {sorted(unknown)}")
        _validate_acyclic_dependencies(self.steps)

    def next_ready_step(self) -> PlanStep | None:
        completed = {s.id for s in self.steps if s.status == StepStatus.DONE}
        return next(
            (
                s
                for s in self.steps
                if s.status == StepStatus.PENDING
                and all(dep in completed for dep in s.depends_on)
            ),
            None,
        )

    def is_complete(self) -> bool:
        return all(
            s.status in {StepStatus.DONE, StepStatus.SKIPPED} for s in self.steps
        )


def _validate_acyclic_dependencies(steps: Iterable[PlanStep]) -> None:
    """Detect cycles with Kahn's algorithm and report an exact residual cycle."""

    dependencies = {step.id: set(step.depends_on) for step in steps}
    dependents: dict[str, set[str]] = {step_id: set() for step_id in dependencies}
    for step_id, required in dependencies.items():
        for dependency in required:
            dependents[dependency].add(step_id)

    ready = deque(step_id for step_id, required in dependencies.items() if not required)
    processed: set[str] = set()
    while ready:
        step_id = ready.popleft()
        processed.add(step_id)
        for dependent in dependents[step_id]:
            dependencies[dependent].remove(step_id)
            if not dependencies[dependent]:
                ready.append(dependent)

    residual = tuple(step_id for step_id in dependencies if step_id not in processed)
    if residual:
        raise DependencyCycleError(_find_cycle(dependencies, residual))


def _find_cycle(
    dependencies: dict[str, set[str]],
    residual: tuple[str, ...],
) -> tuple[str, ...]:
    """Return one cycle from Kahn's residual graph, excluding blocked descendants."""

    visiting: list[str] = []
    positions: dict[str, int] = {}
    visited: set[str] = set()

    def visit(step_id: str) -> tuple[str, ...] | None:
        if step_id in positions:
            start = positions[step_id]
            return tuple(visiting[start:] + [step_id])
        if step_id in visited:
            return None
        visiting.append(step_id)
        positions[step_id] = len(visiting) - 1
        for dependency in dependencies[step_id]:
            if dependency in residual_ids:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
        positions.pop(step_id)
        visiting.pop()
        visited.add(step_id)
        return None

    residual_ids = set(residual)
    for step_id in residual:
        cycle = visit(step_id)
        if cycle is not None:
            return cycle
    raise RuntimeError("Kahn's algorithm found a cycle without a residual cycle path")


@dataclass(slots=True)
class AgentState:
    goal: str
    plan: Plan
    observations: list[Observation] = field(default_factory=list)
    completed: bool = False


StepSelector = Callable[[AgentState, PlanStep], list[ToolCall]]
Replanner = Callable[[AgentState], Plan]


class PlanExecutor:
    """Execute one deterministic plan step for an application-owned workflow.

    Orchestration runtimes such as LangGraph should select and schedule steps. This
    class owns only the bounded transition from a selected step to observations and
    step evidence.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        select_calls: StepSelector,
        *,
        trace: TraceSink,
    ) -> None:
        self.tools = tools
        self.select_calls = select_calls
        self.trace = trace

    def execute_step(self, state: AgentState, step: PlanStep) -> AgentState:
        if step.status != StepStatus.PENDING:
            raise ValueError(f"Plan step is not pending: {step.id}")
        step.status = StepStatus.RUNNING
        self.trace.record("step.started", {"step_id": step.id, "title": step.title})
        try:
            for call in self.select_calls(state, step):
                self.trace.record(
                    "tool.started",
                    {
                        "step_id": step.id,
                        "tool_name": call.tool_name,
                        "arguments": dict(call.arguments),
                    },
                )
                try:
                    observation = self.tools.execute(call)
                except Exception as exc:
                    self.trace.record(
                        "tool.finished",
                        {
                            "step_id": step.id,
                            "tool_name": call.tool_name,
                            "status": "failed",
                            "error": str(exc),
                        },
                    )
                    raise
                self.trace.record(
                    "tool.finished",
                    {
                        "step_id": step.id,
                        "tool_name": call.tool_name,
                        "status": "completed",
                        "observation_source": observation.source,
                    },
                )
                state.observations.append(observation)
                step.evidence.append(observation.content)
            step.status = StepStatus.DONE
        except Exception as exc:
            step.status = StepStatus.FAILED
            step.error = str(exc)
            state.observations.append(
                Observation("runtime", f"Step failed: {step.title}: {exc}")
            )
        self.trace.record(
            "step.finished",
            {"step_id": step.id, "status": step.status.value, "error": step.error},
        )
        return state


class PlanningAgent:
    """Reference synchronous runner for small local workflows.

    Production orchestration should use :class:`PlanExecutor` inside its own graph
    nodes. This runner intentionally remains simple and non-durable.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        select_calls: StepSelector,
        *,
        trace: TraceSink,
        replan: Replanner | None = None,
    ) -> None:
        self.executor = PlanExecutor(tools, select_calls, trace=trace)
        self.trace = trace
        self.replan = replan

    def run(self, state: AgentState, *, max_iterations: int = 20) -> AgentState:
        self.trace.record(
            "agent.run.started",
            {
                "goal": state.goal,
                "plan_goal": state.plan.goal,
                "max_iterations": max_iterations,
            },
        )
        for _ in range(max_iterations):
            if state.plan.is_complete():
                state.completed = True
                self.trace.record(
                    "agent.run.finished",
                    {"completed": True, "reason": "plan_complete"},
                )
                return state
            step = state.plan.next_ready_step()
            if step is None:
                if self.replan is None:
                    self.trace.record(
                        "agent.run.finished",
                        {"completed": False, "reason": "no_ready_step"},
                    )
                    return state
                self.trace.record("plan.replan.started", {"goal": state.goal})
                state.plan = self.replan(state)
                self.trace.record(
                    "plan.replan.finished",
                    {"plan_goal": state.plan.goal, "step_count": len(state.plan.steps)},
                )
                continue
            self.executor.execute_step(state, step)
        self.trace.record(
            "agent.run.finished",
            {"completed": state.completed, "reason": "max_iterations_exceeded"},
        )
        return state

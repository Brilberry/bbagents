from bbagents import (
    AgentState,
    DependencyCycleError,
    FunctionTool,
    Observation,
    Plan,
    PlanExecutor,
    PlanStep,
    StepStatus,
    ToolCall,
    ToolRegistry,
)
from bbagents.observability import InMemoryTrace
from bbagents.reference import PlanningAgent


def test_plan_respects_dependencies_and_executes_tools() -> None:
    tools = ToolRegistry(
        [
            FunctionTool(
                "record", "record evidence", lambda value: Observation("tool", value)
            )
        ]
    )
    trace = InMemoryTrace()
    plan = Plan(
        "ship",
        [
            PlanStep("inspect", "Inspect"),
            PlanStep("change", "Change", depends_on=["inspect"]),
        ],
    )
    state = AgentState("ship", plan)
    agent = PlanningAgent(
        tools,
        lambda state, step: [ToolCall("record", {"value": step.id})],
        trace=trace,
    )

    result = agent.run(state)

    assert result.completed
    assert [observation.content for observation in result.observations] == [
        "inspect",
        "change",
    ]
    assert all(step.status == StepStatus.DONE for step in plan.steps)
    assert len(trace.events_named("agent.run.started")) == 1
    assert len(trace.events_named("agent.run.finished")) == 1
    assert len(trace.events_named("step.finished")) == 2
    assert len(trace.events_named("tool.finished")) == 2


def test_plan_rejects_unknown_dependencies() -> None:
    try:
        Plan("bad", [PlanStep("one", "One", depends_on=["missing"])])
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("invalid plan accepted")


def test_plan_rejects_a_two_step_dependency_cycle() -> None:
    try:
        Plan(
            "bad",
            [
                PlanStep("A", "A", depends_on=["B"]),
                PlanStep("B", "B", depends_on=["A"]),
            ],
        )
    except DependencyCycleError as error:
        assert error.steps == ("A", "B", "A")
        assert str(error) == "Plan dependency cycle: A -> B -> A"
    else:
        raise AssertionError("cyclic plan accepted")


def test_plan_reports_cycle_without_unrelated_blocked_steps() -> None:
    try:
        Plan(
            "bad",
            [
                PlanStep("A", "A", depends_on=["B"]),
                PlanStep("B", "B", depends_on=["A"]),
                PlanStep("C", "C", depends_on=["A"]),
            ],
        )
    except DependencyCycleError as error:
        assert set(error.steps) == {"A", "B"}
        assert "C" not in error.steps
    else:
        raise AssertionError("cyclic plan accepted")


def test_plan_executor_runs_one_explicit_step() -> None:
    trace = InMemoryTrace()
    state = AgentState("ship", Plan("ship", [PlanStep("inspect", "Inspect")]))
    executor = PlanExecutor(
        ToolRegistry(
            [
                FunctionTool(
                    "record",
                    "record",
                    lambda: Observation("tool", "ok"),
                )
            ]
        ),
        lambda state, step: [ToolCall("record")],
        trace=trace,
    )

    result = executor.execute_step(state, state.plan.steps[0])

    assert result is state
    assert state.plan.steps[0].status == StepStatus.DONE
    assert [(item.source, item.content) for item in state.observations] == [("tool", "ok")]

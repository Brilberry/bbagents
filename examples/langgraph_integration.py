"""Use LangGraph for orchestration and bbagents for agent domain primitives."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from bbagents import AgentState, FunctionTool, Observation, Plan, PlanStep, StepStatus, ToolCall, ToolRegistry
from bbagents.observability import InMemoryTrace


class MigrationState(TypedDict):
    """State checkpointed by LangGraph between orchestration nodes."""

    agent: dict[str, Any]
    strategy: str


REPOSITORY = {
    "authentication": "AuthMiddleware owns authentication decisions.",
    "sessions": "SessionStore owns rotation, expiry, and revocation.",
}


def inspect_component(component: str) -> Observation:
    return Observation(
        source="repository.inspect",
        content=REPOSITORY[component],
        metadata={"component": component},
    )


def draft_strategy(evidence: list[str]) -> Observation:
    return Observation(
        source="migration.strategy",
        content=(
            "Extend AuthMiddleware for OAuth and preserve SessionStore as the "
            "session lifecycle boundary."
        ),
        metadata={"evidence": evidence},
    )


def serialize_agent(agent: AgentState) -> dict[str, Any]:
    """Convert bbagents values into checkpoint-safe built-in values."""

    return {
        "goal": agent.goal,
        "completed": agent.completed,
        "plan": {
            "goal": agent.plan.goal,
            "version": agent.plan.version,
            "steps": [
                {
                    "id": step.id,
                    "title": step.title,
                    "description": step.description,
                    "status": step.status.value,
                    "depends_on": list(step.depends_on),
                    "evidence": list(step.evidence),
                    "error": step.error,
                }
                for step in agent.plan.steps
            ],
        },
        "observations": [
            {
                "source": observation.source,
                "content": observation.content,
                "metadata": observation.metadata,
            }
            for observation in agent.observations
        ],
    }


def deserialize_agent(value: dict[str, Any]) -> AgentState:
    """Restore typed bbagents values inside a graph node."""

    plan_value = value["plan"]
    plan = Plan(
        plan_value["goal"],
        [
            PlanStep(
                id=step["id"],
                title=step["title"],
                description=step["description"],
                status=StepStatus(step["status"]),
                depends_on=list(step["depends_on"]),
                evidence=list(step["evidence"]),
                error=step["error"],
            )
            for step in plan_value["steps"]
        ],
        version=plan_value["version"],
    )
    return AgentState(
        goal=value["goal"],
        plan=plan,
        observations=[
            Observation(
                source=observation["source"],
                content=observation["content"],
                metadata=dict(observation["metadata"]),
            )
            for observation in value["observations"]
        ],
        completed=value["completed"],
    )


def build_graph() -> tuple[Any, InMemoryTrace]:
    """Compile a checkpointed graph whose nodes use bbagents primitives."""

    tools = ToolRegistry(
        [
            FunctionTool(
                "inspect_component",
                "Inspect one repository component.",
                inspect_component,
            ),
            FunctionTool(
                "draft_strategy",
                "Draft an OAuth strategy from inspected evidence.",
                draft_strategy,
            ),
        ]
    )
    trace = InMemoryTrace()

    def execute_step(
        state: MigrationState,
        step_id: str,
        call: ToolCall,
    ) -> MigrationState:
        agent = deserialize_agent(state["agent"])
        step = next(item for item in agent.plan.steps if item.id == step_id)
        completed = {
            item.id for item in agent.plan.steps if item.status == StepStatus.DONE
        }
        if not set(step.depends_on) <= completed:
            raise RuntimeError(f"Step dependencies are not complete: {step_id}")

        step.status = StepStatus.RUNNING
        trace.record("step.started", {"step_id": step.id, "title": step.title})
        observation = tools.execute(call)
        agent.observations.append(observation)
        step.evidence.append(observation.content)
        step.status = StepStatus.DONE
        agent.completed = agent.plan.is_complete()
        trace.record("step.finished", {"step_id": step.id, "status": step.status.value})
        return {
            "agent": serialize_agent(agent),
            "strategy": (
                observation.content
                if observation.source == "migration.strategy"
                else state["strategy"]
            ),
        }

    def inspect_authentication(state: MigrationState) -> MigrationState:
        return execute_step(
            state,
            "authentication",
            ToolCall("inspect_component", {"component": "authentication"}),
        )

    def inspect_sessions(state: MigrationState) -> MigrationState:
        return execute_step(
            state,
            "sessions",
            ToolCall("inspect_component", {"component": "sessions"}),
        )

    def produce_strategy(state: MigrationState) -> MigrationState:
        agent = deserialize_agent(state["agent"])
        evidence = [observation.content for observation in agent.observations]
        return execute_step(
            state,
            "strategy",
            ToolCall("draft_strategy", {"evidence": evidence}),
        )

    builder = StateGraph(MigrationState)
    builder.add_node("inspect_authentication", inspect_authentication)
    builder.add_node("inspect_sessions", inspect_sessions)
    builder.add_node("produce_strategy", produce_strategy)
    builder.add_edge(START, "inspect_authentication")
    builder.add_edge("inspect_authentication", "inspect_sessions")
    builder.add_edge("inspect_sessions", "produce_strategy")
    builder.add_edge("produce_strategy", END)
    return builder.compile(checkpointer=InMemorySaver()), trace


def initial_state() -> MigrationState:
    """Create the bbagents plan stored inside LangGraph state."""

    plan = Plan(
        "Plan an OAuth migration",
        [
            PlanStep("authentication", "Inspect authentication"),
            PlanStep(
                "sessions",
                "Inspect session handling",
                depends_on=["authentication"],
            ),
            PlanStep(
                "strategy",
                "Produce migration strategy",
                depends_on=["authentication", "sessions"],
            ),
        ],
    )
    agent = AgentState("Plan an OAuth migration", plan)
    return {"agent": serialize_agent(agent), "strategy": ""}


def run_example() -> tuple[MigrationState, InMemoryTrace, int]:
    """Invoke the graph and report how many checkpoints it retained."""

    graph, trace = build_graph()
    config = {"configurable": {"thread_id": "oauth-migration-example"}}
    result = graph.invoke(initial_state(), config)
    checkpoint_count = len(list(graph.get_state_history(config)))
    return result, trace, checkpoint_count


def main() -> None:
    state, trace, checkpoint_count = run_example()
    agent = deserialize_agent(state["agent"])

    print(f"Plan complete: {agent.completed}")
    print(f"Strategy: {state['strategy']}")
    print(f"Trace events: {len(trace.events)}")
    print(f"LangGraph checkpoints: {checkpoint_count}")


if __name__ == "__main__":
    main()

"""Plan repository inspection before an OAuth migration."""

from __future__ import annotations

from bbagents import AgentState, FunctionTool, Observation, Plan, PlanStep, TraceSink, ToolCall, ToolRegistry
from bbagents.observability import InMemoryTrace
from bbagents.reference import PlanningAgent


REPOSITORY = {
    "authentication": (
        "AuthMiddleware validates signed sessions before requests reach handlers."
    ),
    "sessions": (
        "SessionStore owns session creation, rotation, expiry, and revocation."
    ),
    "routing": (
        "AuthRoutes registers login and logout handlers under the /auth prefix."
    ),
    "configuration": (
        "AuthSettings loads provider configuration from environment-backed secrets."
    ),
}


def inspect_component(component: str) -> Observation:
    """Return evidence about one known repository component."""

    return Observation(
        source="repository.inspect",
        content=REPOSITORY[component],
        metadata={"component": component},
    )


def search_repository(query: str) -> Observation:
    """Search the in-memory repository using simple term matching."""

    terms = set(query.casefold().split())
    matches = [
        name
        for name, description in REPOSITORY.items()
        if terms & set(f"{name} {description}".casefold().split())
    ]
    return Observation(
        source="repository.search",
        content=f"Matching components: {', '.join(matches)}",
        metadata={"query": query, "matches": matches},
    )


def draft_migration_strategy(
    authentication: str,
    sessions: str,
    extension_points: str,
) -> Observation:
    """Turn inspected evidence into a migration strategy."""

    strategy = (
        "1. Extend AuthMiddleware with OAuth callback validation. "
        "2. Preserve SessionStore as the only session lifecycle boundary. "
        "3. Register callback routes through AuthRoutes. "
        "4. Load provider credentials through AuthSettings."
    )
    return Observation(
        source="migration.strategy",
        content=strategy,
        metadata={
            "authentication_evidence": authentication,
            "session_evidence": sessions,
            "extension_point_evidence": extension_points,
        },
    )


def build_agent(
    trace: TraceSink | None = None,
) -> tuple[PlanningAgent, AgentState, TraceSink]:
    """Construct the tools, plan, state, and deterministic step selector."""

    tools = ToolRegistry(
        [
            FunctionTool(
                "inspect_component",
                "Inspect a named repository component.",
                inspect_component,
            ),
            FunctionTool(
                "search_repository",
                "Find components related to an architectural concern.",
                search_repository,
            ),
            FunctionTool(
                "draft_migration_strategy",
                "Draft an OAuth migration strategy from repository evidence.",
                draft_migration_strategy,
            ),
        ]
    )
    trace = trace or InMemoryTrace()
    state = AgentState(
        goal="Inspect authentication and plan an OAuth migration",
        plan=Plan(
            "Inspect authentication and plan an OAuth migration",
            [
                PlanStep("authentication", "Inspect authentication architecture"),
                PlanStep(
                    "sessions",
                    "Inspect session lifecycle",
                    depends_on=["authentication"],
                ),
                PlanStep(
                    "extension_points",
                    "Find OAuth extension points",
                    depends_on=["authentication", "sessions"],
                ),
                PlanStep(
                    "strategy",
                    "Draft the migration strategy",
                    depends_on=["extension_points"],
                ),
            ],
        ),
    )

    def evidence_for(component: str) -> str:
        return next(
            observation.content
            for observation in state.observations
            if observation.metadata.get("component") == component
        )

    def select_calls(state: AgentState, step: PlanStep) -> list[ToolCall]:
        if step.id in {"authentication", "sessions"}:
            return [ToolCall("inspect_component", {"component": step.id})]
        if step.id == "extension_points":
            return [
                ToolCall(
                    "search_repository",
                    {"query": "auth routing configuration provider"},
                )
            ]
        if step.id == "strategy":
            extension_points = next(
                observation.content
                for observation in state.observations
                if observation.source == "repository.search"
            )
            return [
                ToolCall(
                    "draft_migration_strategy",
                    {
                        "authentication": evidence_for("authentication"),
                        "sessions": evidence_for("sessions"),
                        "extension_points": extension_points,
                    },
                )
            ]
        raise ValueError(f"No tool selection rule for step: {step.id}")

    return PlanningAgent(tools, select_calls, trace=trace), state, trace


def run_example() -> tuple[AgentState, InMemoryTrace]:
    """Execute the complete example and return inspectable state and trace."""

    agent, state, trace = build_agent()
    assert isinstance(trace, InMemoryTrace)
    return agent.run(state), trace


def main() -> None:
    state, trace = run_example()

    print(f"Goal: {state.goal}")
    print(f"Plan complete: {state.completed}")
    print("\nSteps:")
    for step in state.plan.steps:
        print(f"- {step.title}: {step.status.value}")

    print("\nMigration strategy:")
    print(state.observations[-1].content)
    print(f"\nTrace events: {len(trace.events)}")


if __name__ == "__main__":
    main()

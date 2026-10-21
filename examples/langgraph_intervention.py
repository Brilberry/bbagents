"""Pause a governed publication action in LangGraph for a human response."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from bbagents import Action, ActionRuntime, ActionStatus
from bbagents.intervention import (
    ActionInterventionPolicy,
    ActionInterventionService,
    HumanOrDelegatedAuthority,
    InMemoryInterventionStore,
)
from bbagents.integrations.langgraph import LangGraphIntervention


class ReleaseState(TypedDict):
    version: str
    intervention_id: str | None
    status: str
    publication: str | None


def build_graph() -> tuple[Any, list[str]]:
    """Build a graph that pauses before a simulated package publication."""

    published_versions: list[str] = []
    service = ActionInterventionService(
        InMemoryInterventionStore(),
        HumanOrDelegatedAuthority(),
    )
    runtime = ActionRuntime(
        {"publish_release": lambda version: published_versions.append(version)},
        policy=ActionInterventionPolicy(service),
    )
    intervention = LangGraphIntervention(service)

    def request_publication(state: ReleaseState) -> ReleaseState:
        result = runtime.execute(
            Action(
                "publish_release",
                {"version": state["version"]},
                expected_effect="publish an immutable package release",
                requires_confirmation=True,
                metadata={"resource": "package-index"},
            )
        )
        if result.status != ActionStatus.PENDING_INTERVENTION:
            raise RuntimeError(f"Expected pending intervention, got {result.status.value}")
        return {
            **state,
            "intervention_id": result.intervention_request_id,
            "status": "awaiting_human_intervention",
        }

    def await_human(state: ReleaseState) -> ReleaseState:
        request_id = state["intervention_id"]
        if request_id is None:
            raise RuntimeError("intervention request ID was not persisted")
        intervention.await_response(request_id)
        return {**state, "status": "approved"}

    def publish(state: ReleaseState) -> ReleaseState:
        result = runtime.execute(
            Action(
                "publish_release",
                {"version": state["version"]},
                expected_effect="publish an immutable package release",
                requires_confirmation=True,
                intervention_id=state["intervention_id"],
                metadata={"resource": "package-index"},
            )
        )
        if not result.success:
            raise RuntimeError(result.error)
        return {**state, "status": "released", "publication": "package-index"}

    builder = StateGraph(ReleaseState)
    builder.add_node("request_publication", request_publication)
    builder.add_node("await_human", await_human)
    builder.add_node("publish", publish)
    builder.add_edge(START, "request_publication")
    builder.add_edge("request_publication", "await_human")
    builder.add_edge("await_human", "publish")
    builder.add_edge("publish", END)
    return builder.compile(checkpointer=InMemorySaver()), published_versions


def run_example() -> tuple[ReleaseState, list[str]]:
    """Run the graph, supply a human response, and return its final state."""

    graph, published_versions = build_graph()
    config = {"configurable": {"thread_id": "bbagents-release"}}
    initial: ReleaseState = {
        "version": "0.1.0",
        "intervention_id": None,
        "status": "draft",
        "publication": None,
    }
    paused = graph.invoke(initial, config)
    if "__interrupt__" not in paused:
        raise RuntimeError("LangGraph did not pause for human intervention")
    result = graph.invoke(
        Command(
            resume={
                "actor_id": "release-approver",
                "actor_kind": "human",
                "decision": "approve",
                "request_version": 1,
                "rationale": "validated release",
            }
        ),
        config,
    )
    return result, published_versions


def main() -> None:
    state, published_versions = run_example()
    print(f"Status: {state['status']}")
    print(f"Published versions: {published_versions}")


if __name__ == "__main__":
    main()

"""Assign different permitted local models to specialized organizational roles."""

from __future__ import annotations

from bbagents.context import Context
from bbagents.decision import DecisionRequest, DecisionRequirements
from bbagents.models import (
    AgentDecisionRuntime,
    AgentProfile,
    CheapestEligibleModelSelector,
    ModelCandidate,
    ModelCatalog,
    ModelProfile,
)
from bbagents.observability import InMemoryTrace
from bbagents.organization import Role


class FixedDecisionModel:
    """Offline stand-in for a model adapter used by this deterministic example."""

    def __init__(self, response: str) -> None:
        self.response = response

    def decide(self, request: DecisionRequest[str]) -> str:
        del request
        return self.response


def run_example() -> tuple[str, str, list[dict[str, object]]]:
    """Draft with a fast model, then review with an independent model."""

    catalog = ModelCatalog(
        [
            ModelCandidate(
                "fast-local",
                ModelProfile("fast-local", 1, 1, 0.2, 0.65),
                FixedDecisionModel("Draft: bbagents 0.1.0 is ready for release."),
            ),
            ModelCandidate(
                "review-local",
                ModelProfile("review-local", 4, 4, 1.2, 0.9),
                FixedDecisionModel("Review: release evidence is sufficient."),
            ),
        ]
    )
    writer = AgentProfile(
        "release-writer",
        Role("release-writer", ("draft release notes",)),
        ("fast-local", "review-local"),
    )
    reviewer = AgentProfile(
        "release-reviewer",
        Role("release-reviewer", ("independently review the release",)),
        ("fast-local", "review-local"),
    )
    trace = InMemoryTrace()
    runtime = AgentDecisionRuntime(
        catalog,
        CheapestEligibleModelSelector(),
        trace=trace,
    )
    request = DecisionRequest(
        task="Produce a release assessment.",
        system="Return a concise release assessment.",
        context=Context("Tests, linting, and type checks passed.", ()),
        schema=str,
        requirements=DecisionRequirements(minimum_quality=0.6),
    )

    draft = runtime.decide(writer, request)
    review = runtime.decide(
        reviewer,
        request,
        excluded_model_ids={draft.model_id},
    )
    selections = [event.payload for event in trace.events_named("agent.model.selected")]
    return draft.decision.value, review.decision.value, selections


def main() -> None:
    draft, review, selections = run_example()
    print(draft)
    print(review)
    print(f"Model selections: {selections}")


if __name__ == "__main__":
    main()

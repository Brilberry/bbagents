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


class StaticDecisionModel:
    def __init__(self, response: str) -> None:
        self.response = response

    def decide(self, request: DecisionRequest[str]) -> str:
        del request
        return self.response


def test_agent_profiles_select_models_and_support_independent_review() -> None:
    catalog = ModelCatalog(
        [
            ModelCandidate(
                "fast-local",
                ModelProfile("fast-local", 1, 1, 0.2, 0.65),
                StaticDecisionModel("draft"),
            ),
            ModelCandidate(
                "deep-local",
                ModelProfile("deep-local", 4, 4, 1.0, 0.9),
                StaticDecisionModel("review"),
            ),
        ]
    )
    writer = AgentProfile(
        "writer",
        Role("writer", ("draft release notes",)),
        ("fast-local", "deep-local"),
    )
    reviewer = AgentProfile(
        "reviewer",
        Role("reviewer", ("independently review release notes",)),
        ("fast-local", "deep-local"),
    )
    trace = InMemoryTrace()
    runtime = AgentDecisionRuntime(
        catalog,
        CheapestEligibleModelSelector(),
        trace=trace,
    )
    request = DecisionRequest(
        task="Draft or review the release notes.",
        system="Return a concise result.",
        context=Context("Validated release evidence.", ()),
        schema=str,
        requirements=DecisionRequirements(minimum_quality=0.6),
    )

    draft = runtime.decide(writer, request)
    review = runtime.decide(
        reviewer,
        request,
        excluded_model_ids={draft.model_id},
    )

    assert draft.model_id == "fast-local"
    assert draft.decision.value == "draft"
    assert review.model_id == "deep-local"
    assert review.decision.value == "review"
    assert len(trace.events_named("agent.model.selected")) == 2

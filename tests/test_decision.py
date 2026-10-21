from bbagents.context import Context
from bbagents.decision import (
    DecisionOutput,
    DecisionFailure,
    DecisionRequest,
    DecisionRuntime,
    DecisionUsage,
    LanguageModelDecisionAdapter,
)
from bbagents.observability import InMemoryTrace


class FakeLanguageModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.users: list[str] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_format: type[str] | None = None,
    ) -> str:
        del system, response_format
        self.users.append(user)
        return self.responses.pop(0)


def test_decision_runtime_uses_bounded_context_and_records_trace() -> None:
    model = FakeLanguageModel(["publish"])
    trace = InMemoryTrace()
    runtime = DecisionRuntime(LanguageModelDecisionAdapter(model), trace=trace)

    result = runtime.decide(
        DecisionRequest(
            task="Choose a release action.",
            system="Return one permitted action.",
            context=Context("All quality checks passed.", ()),
            schema=str,
            validate=lambda value: value == "publish",
        )
    )

    assert result.value == "publish"
    assert result.attempts == 1
    assert model.users == [
        "Task:\nChoose a release action.\n\nContext:\nAll quality checks passed."
    ]
    assert len(trace.events_named("decision.started")) == 1
    assert len(trace.events_named("decision.completed")) == 1


def test_decision_runtime_retries_invalid_proposals() -> None:
    model = FakeLanguageModel(["unsafe", "publish"])
    runtime = DecisionRuntime(LanguageModelDecisionAdapter(model))

    result = runtime.decide(
        DecisionRequest(
            task="Choose a release action.",
            system="Return one permitted action.",
            context=Context("", ()),
            schema=str,
            validate=lambda value: value == "publish",
        ),
        retries=1,
    )

    assert result.value == "publish"
    assert result.attempts == 2


def test_decision_runtime_raises_after_validation_failures() -> None:
    runtime = DecisionRuntime(
        LanguageModelDecisionAdapter(FakeLanguageModel(["unsafe"]))
    )

    try:
        runtime.decide(
            DecisionRequest(
                task="Choose a release action.",
                system="Return one permitted action.",
                context=Context("", ()),
                schema=str,
                validate=lambda value: value == "publish",
            )
        )
    except DecisionFailure as error:
        assert "Decision failed after 1 attempts" in str(error)
    else:
        raise AssertionError("invalid decision was accepted")


class UppercaseSchema:
    name = "uppercase_text"
    provider_format = None

    def parse(self, value: object) -> str:
        if not isinstance(value, str) or value != value.upper():
            raise ValueError("expected uppercase text")
        return value


class MetadataDecisionModel:
    def decide(self, request: DecisionRequest[str]) -> DecisionOutput[str]:
        del request
        return DecisionOutput(
            "PUBLISH",
            model_id="local-reviewer",
            usage=DecisionUsage(
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
            ),
            metadata={"provider_request_id": "request-17"},
        )


def test_decision_runtime_uses_custom_schema_and_preserves_model_metadata() -> None:
    result = DecisionRuntime(MetadataDecisionModel()).decide(
        DecisionRequest(
            task="Choose a release action.",
            system="Return uppercase text.",
            context=Context("", ()),
            schema=UppercaseSchema(),
        )
    )

    assert result.value == "PUBLISH"
    assert result.model_id == "local-reviewer"
    assert result.usage == DecisionUsage(10, 2, 12)
    assert result.metadata == {"provider_request_id": "request-17"}

import pytest

from bbagents.context import Context
from bbagents.decision import DecisionFailure, DecisionRequest, DecisionRuntime
from bbagents.integrations.ollama import OllamaDecisionModel


def test_adapter_preserves_messages_usage_and_model():
    calls = []

    def transport(url, payload, timeout):
        calls.append((url, payload, timeout))
        return {
            "model": "local-1",
            "message": {"content": "verified"},
            "done": True,
            "prompt_eval_count": 3,
            "eval_count": 2,
        }

    result = DecisionRuntime(OllamaDecisionModel("local", transport=transport)).decide(
        DecisionRequest("summarize", "facts only", Context("evidence", ()), str)
    )
    assert result.value == "verified" and result.usage.total_tokens == 5
    assert result.model_id == "local-1"
    assert calls[0][1]["stream"] is False
    assert calls[0][1]["messages"][0]["content"] == "facts only"


def test_incomplete_generation_is_not_a_successful_decision():
    model = OllamaDecisionModel("local", transport=lambda *args: {"done": False})
    with pytest.raises(DecisionFailure, match="did not complete"):
        DecisionRuntime(model).decide(
            DecisionRequest("task", "system", Context("", ()), str)
        )


def test_json_schema_is_sent_and_output_is_parsed_before_validation():
    class Schema:
        name = "assessment"
        provider_format = {
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
        }

        def parse(self, value):
            if not isinstance(value, dict) or type(value.get("ready")) is not bool:
                raise ValueError("Invalid assessment")
            return value

    def transport(url, payload, timeout):
        assert payload["format"] == Schema.provider_format
        return {"done": True, "message": {"content": '{"ready": true}'}}

    result = DecisionRuntime(OllamaDecisionModel("local", transport=transport)).decide(
        DecisionRequest("assess", "facts only", Context("", ()), Schema())
    )
    assert result.value == {"ready": True}

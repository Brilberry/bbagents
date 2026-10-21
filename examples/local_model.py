"""Run offline with a fixture, or pass --model NAME for an installed Ollama model."""

import argparse

from bbagents.context import Context
from bbagents.decision import DecisionRequest, DecisionRuntime
from bbagents.integrations.ollama import OllamaDecisionModel


def fixture_transport(url, payload, timeout):
    return {
        "model": "fixture",
        "message": {"content": "Release evidence verified."},
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 4,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    args = parser.parse_args()
    model = (
        OllamaDecisionModel(args.model)
        if args.model
        else OllamaDecisionModel("fixture", transport=fixture_transport)
    )
    result = DecisionRuntime(model).decide(
        DecisionRequest(
            "Summarize the evidence",
            "Use only supplied facts.",
            Context("Tests passed.", ()),
            str,
        )
    )
    print(result.value)
    print("Model:", result.model_id, "Usage:", result.usage)


if __name__ == "__main__":
    main()

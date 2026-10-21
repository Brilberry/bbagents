"""Optional local Ollama /api/chat adapter; no provider SDK required.

Protocol: https://docs.ollama.com/api/chat . No model is downloaded or selected
implicitly. Socket timeout bounds network operations, not server-side generation.
"""

import json
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from ..decision import DecisionOutput, DecisionRequest, DecisionUsage

Transport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


def _post(url: str, payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    request = Request(
        url,
        json.dumps(dict(payload)).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Model response exceeds size limit")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Expected an Ollama response object")
    return result


class OllamaDecisionModel:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 60,
        transport: Transport = _post,
    ) -> None:
        self.model = model
        self.url = base_url.rstrip("/") + "/api/chat"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def decide(self, request: DecisionRequest[Any]) -> DecisionOutput[Any]:
        format_value = request.output_schema().provider_format
        if format_value is not None and not isinstance(format_value, Mapping):
            raise ValueError(
                "Use a JSON-schema DecisionSchema for structured Ollama output"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": request.system},
                {
                    "role": "user",
                    "content": f"Task: {request.task}\n\n{request.context.content}",
                },
            ],
        }
        if format_value is not None:
            payload["format"] = dict(format_value)
        response = self.transport(self.url, payload, self.timeout_seconds)
        if response.get("done") is not True:
            raise ValueError("Model response did not complete")
        content = response["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Model content must be text")
        input_tokens = response.get("prompt_eval_count")
        output_tokens = response.get("eval_count")
        return DecisionOutput(
            json.loads(content) if format_value is not None else content,
            model_id=str(response.get("model", self.model)),
            usage=DecisionUsage(
                input_tokens,
                output_tokens,
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None,
            ),
            metadata={"done_reason": response.get("done_reason")},
        )

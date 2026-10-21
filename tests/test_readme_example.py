"""Keep the public README example executable without a running model server."""

from pathlib import Path

from bbagents.integrations import ollama


def test_readme_example(monkeypatch, capsys):
    requests = []

    def fixture_transport(url, payload, timeout):
        requests.append(payload)
        return {
            "model": "readme-fixture",
            "message": {
                "content": "Extend AuthMiddleware and preserve SessionStore ownership.",
            },
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 8,
        }

    real_model = ollama.OllamaDecisionModel
    monkeypatch.setattr(
        ollama,
        "OllamaDecisionModel",
        lambda model: real_model(model, transport=fixture_transport),
    )
    monkeypatch.setenv("OLLAMA_MODEL", "readme-fixture")
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    code = readme.split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    exec(compile(code, "README.md", "exec"), namespace)

    state = namespace["state"]
    assert state.completed
    assert [item.metadata["component"] for item in state.observations] == [
        "authentication", "sessions",
    ]
    assert len(requests) == 1
    prompt = requests[0]["messages"][1]["content"]
    assert "AuthMiddleware validates signed sessions." in prompt
    assert "SessionStore owns creation" in prompt
    assert namespace["decision"].usage.total_tokens == 28
    assert namespace["trace"].events
    assert "Completed steps: 2; trace events:" in capsys.readouterr().out

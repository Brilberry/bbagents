# Demos

## Autonomous Release Manager

[`autonomous_release_manager.py`](autonomous_release_manager.py) prepares a release
of `bbagents` itself. It reads the real package metadata and runs the package's
tests, linting, and type checks. It then retrieves retained release procedure,
constructs a bounded context for drafting release notes, records a durable release
state, and requires a distinct release-approver role before publication.

The publication adapter is intentionally simulated. Replacing it with PyPI, an
internal package index, or a CI release API is an application concern; the release
plan, evidence, approval, capability check, trace, and state record do not change.

Run from the repository root:

```console
python demos/autonomous_release_manager.py
python demos/autonomous_release_manager.py --resume --approve
```

The first command saves a pending intervention. The second restores it, records a
demo human approval, and runs simulated publication. State defaults to
`.bbagents/release`; use `--state-dir PATH` for a different directory. Publication
results are journaled so restart/replay reconciles the index before repeating work.
Use a fresh directory for a separate simulated release.

The demo uses the framework's intervention contract. A production application
replaces the in-memory channel, store, and authority resolver with an authenticated UI,
LangGraph interrupt, ticketing system, or another human-in-the-loop integration.

For real local-model notes, install a model in Ollama and use
`--model MODEL_NAME` (optionally `--model-url http://127.0.0.1:11434`). This generates
notes only; it cannot authorize publication. The adapter follows the
[Ollama chat API](https://docs.ollama.com/api/chat).

## Pandemic Response Fixture

```console
python demos/pandemic_response.py
```

A fictional response allocation requires human approval, survives restart, and
reuses a journaled result. It uses the same task, approval, persistence and recovery
contracts as the release manager, with no domain behavior in the library. This is
an integration fixture, not the full game or a scientific disease simulation.

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
python demos/autonomous_release_manager.py --approve
```

The first command stops at `awaiting_human_intervention` and delivers an intervention
request to the in-memory channel. The second submits a response from the demo's human
approver and exercises the simulated publication action.

The demo uses the framework's intervention contract. A production application
replaces the in-memory channel, store, and authority resolver with an authenticated UI,
LangGraph interrupt, ticketing system, or another human-in-the-loop integration.

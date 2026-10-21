"""Run the OAuth planner locally or export its trace to Langfuse when configured."""

from __future__ import annotations

import os

from oauth_migration_planner import build_agent

from bbagents import TraceSink
from bbagents.observability import InMemoryTrace
from bbagents.integrations.langfuse import LangfuseTrace


def default_trace() -> TraceSink:
    """Use Langfuse when configured; otherwise keep the complete trace locally."""

    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return LangfuseTrace()
    return InMemoryTrace()


def main() -> None:
    trace = default_trace()
    agent, state, _ = build_agent(trace)
    agent.run(state)

    print(f"Plan complete: {state.completed}")
    if isinstance(trace, LangfuseTrace):
        trace.flush()
        print("Published one trace with nested step and tool observations to Langfuse.")
    else:
        print(f"Recorded {len(trace.events)} local trace events.")
        print("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to export this run.")


if __name__ == "__main__":
    main()

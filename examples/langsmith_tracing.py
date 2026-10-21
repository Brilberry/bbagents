"""Send the OAuth migration planner's complete trace to LangSmith.

Set ``LANGSMITH_API_KEY`` before running this example. LangSmith's standard
environment configuration selects the endpoint, project, and sampling policy.
"""

from __future__ import annotations

from oauth_migration_planner import build_agent

from bbagents.integrations.langsmith import LangSmithTrace


def main() -> None:
    trace = LangSmithTrace(project_name="bbagents-examples")
    agent, state, _ = build_agent(trace)
    agent.run(state)

    print(f"Plan complete: {state.completed}")
    print("Published one trace with nested step and tool spans to LangSmith.")


if __name__ == "__main__":
    main()

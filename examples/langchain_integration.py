"""Use LangChain models and tools inside a deterministic bbagents plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool

from bbagents import AgentState, Observation, Plan, PlanStep, ToolCall, ToolRegistry
from bbagents.context import Context
from bbagents.decision import DecisionRequest, DecisionRuntime, LanguageModelDecisionAdapter
from bbagents.observability import InMemoryTrace
from bbagents.reference import PlanningAgent


@dataclass(slots=True)
class LangChainChatModelAdapter:
    """Expose a LangChain chat model through the bbagents model protocol."""

    model: BaseChatModel

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_format: type[Any] | None = None,
    ) -> Any | str:
        messages = [SystemMessage(system), HumanMessage(user)]
        if response_format is not None:
            return self.model.with_structured_output(response_format).invoke(messages)

        response = self.model.invoke(messages)
        if isinstance(response.content, str):
            return response.content
        return response.text


class LangChainToolAdapter:
    """Expose a LangChain tool through the bbagents tool protocol."""

    def __init__(self, tool: BaseTool) -> None:
        self.tool = tool
        self.name = tool.name
        self.description = tool.description

    def run(self, **kwargs: Any) -> Observation:
        result = self.tool.invoke(kwargs)
        return Observation(source=self.name, content=str(result))


REPOSITORY = {
    "authentication": (
        "Authentication is centralized in AuthMiddleware and uses signed sessions."
    ),
    "sessions": (
        "Session creation is isolated behind SessionStore.create and rotate."
    ),
}


def inspect_component(component: str) -> str:
    """Inspect a repository component before planning an OAuth migration."""

    return REPOSITORY[component]


def build_agent() -> tuple[PlanningAgent, AgentState, InMemoryTrace]:
    langchain_tool = StructuredTool.from_function(
        func=inspect_component,
        name="inspect_component",
        description="Inspect one authentication-related repository component.",
    )
    tools = ToolRegistry([LangChainToolAdapter(langchain_tool)])
    trace = InMemoryTrace()
    state = AgentState(
        goal="Inspect authentication before an OAuth migration",
        plan=Plan(
            "Inspect authentication before an OAuth migration",
            [
                PlanStep("authentication", "Inspect authentication architecture"),
                PlanStep(
                    "sessions",
                    "Inspect session handling",
                    depends_on=["authentication"],
                ),
            ],
        ),
    )

    def select_calls(state: AgentState, step: PlanStep) -> list[ToolCall]:
        del state
        return [ToolCall("inspect_component", {"component": step.id})]

    agent = PlanningAgent(tools, select_calls, trace=trace)
    return agent, state, trace


def main() -> None:
    agent, state, trace = build_agent()
    agent.run(state)

    model = LangChainChatModelAdapter(
        FakeListChatModel(
            responses=[
                "OAuth should extend AuthMiddleware and preserve SessionStore as the "
                "session boundary."
            ]
        )
    )
    evidence = "\n".join(f"- {item.content}" for item in state.observations)
    recommendation = DecisionRuntime(
        LanguageModelDecisionAdapter(model),
        trace=trace,
    ).decide(
        DecisionRequest(
            task="Summarize repository evidence for an OAuth migration.",
            system="Produce a concise migration recommendation.",
            context=Context(evidence, ()),
            schema=str,
        )
    ).value

    print(f"Plan complete: {state.completed}")
    print("Observations:")
    print(evidence)
    print(f"Recommendation: {recommendation}")
    print(f"Trace events: {len(trace.events)}")


if __name__ == "__main__":
    main()

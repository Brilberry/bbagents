# bbagents

Python building blocks for agent systems: tools, planning, model selection, memory,
human approval, tracing, and evaluation. Works with LangGraph or your own runtime.

Companion code for *Agent Engineering*. [Buy the book](https://press.brilberry.com).

Requires Python 3.11+.

```sh
pip install -e '.[dev]'
pytest
```

## Example

Inspect a repository before recommending an OAuth migration. This uses sample
repository data and a local Ollama model; replace the tool with your repository
reader. Run Ollama with an installed model and set `OLLAMA_MODEL` to its name.

```python
import os

from bbagents import (
    AgentState, DecisionRequest, DecisionRuntime, FunctionTool,
    Observation, Plan, PlanStep, ToolCall, ToolRegistry,
)
from bbagents.context import Context
from bbagents.integrations.ollama import OllamaDecisionModel
from bbagents.observability import InMemoryTrace
from bbagents.reference import PlanningAgent

repository = {
    "authentication": "AuthMiddleware validates signed sessions.",
    "sessions": "SessionStore owns creation, rotation, expiry, and revocation.",
}


def inspect_component(component: str) -> Observation:
    return Observation(
        source="repository.inspect",
        content=repository[component],
        metadata={"component": component},
    )


tools = ToolRegistry([
    FunctionTool("inspect", "Inspect a repository component", inspect_component),
])
goal = "Inspect authentication before recommending an OAuth migration"
state = AgentState(goal, Plan(goal, [
    PlanStep("authentication", "Inspect authentication"),
    PlanStep("sessions", "Inspect session ownership", depends_on=["authentication"]),
]))
trace = InMemoryTrace()


def select_calls(state: AgentState, step: PlanStep) -> list[ToolCall]:
    return [ToolCall("inspect", {"component": step.id})]


# Deterministic inspection first; the model receives the collected evidence.
PlanningAgent(tools, select_calls, trace=trace).run(state)
assert state.completed

decision = DecisionRuntime(
    OllamaDecisionModel(os.environ["OLLAMA_MODEL"]),
    trace=trace,
).decide(
    DecisionRequest(
        task="Recommend where to integrate OAuth without duplicating session logic.",
        system="Use only the supplied evidence. Flag anything needing inspection.",
        context=Context("\n".join(item.content for item in state.observations), ()),
        schema=str,
        validate=lambda text: bool(text.strip()),
    ),
    retries=1,
)

print(decision.value)
print(f"Model: {decision.model_id}; usage: {decision.usage}")
print(f"Completed steps: {len(state.plan.steps)}; trace events: {len(trace.events)}")
```

The reference runner is for small, local workflows. Use LangGraph for durable
orchestration; the same contracts can run inside graph nodes.

## Features

- Typed tools, dependency-checked plans, structured model decisions and validation.
- Per-agent model selection, routing constraints, cost estimates and caching.
- Capability policies, human intervention, explicit delegation and LangGraph interrupts.
- Bounded async execution, retry policies, execution journals and effect reconciliation.
- Versioned checkpoints, SQLite persistence, task budgets and completion gates.
- Budgeted context, summarization contracts and provider-neutral retrieval (RAG).
- Scoped, revisioned memory and skills; evaluated and approved procedure promotion.
- Roles, messages, delegation and organizational governance.
- Structured traces, isolated evaluation trials and compaction-continuation checks.
- Optional Ollama, LangChain, LangGraph, LangSmith and Langfuse integrations.

No mandatory runtime dependencies. Applications own domain behavior and adapters.

See [examples](examples/), [demos](demos/) and the [guide](GUIDE.md).

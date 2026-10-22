# bbagents

Python building blocks for agent systems: tools, planning, model selection, memory,
human approval, tracing, and evaluation. Works with LangGraph or your own runtime.

Companion code for *Agentic AI: Architecture, Theory, and Practice*.
[Buy the book](https://press.brilberry.com).

<a href="https://press.brilberry.com">
  <img src="assets/book-cover.png" alt="Agentic AI: Architecture, Theory, and Practice by Marco Dinacci" width="240" height="360">
</a>

Requires Python 3.11+.

```sh
pip install -e '.[dev]'
pytest
```

## Survival Island example

Five autonomous survivors explore a seeded 30×30 island of forests, rivers,
mountains, and grassland. Each has health, hunger, thirst, energy, an inventory,
a personal objective, a limited view of the world, remembered terrain and
resources, and a message inbox. They gather wood and berries, drink from rivers,
craft tools, and build shelters while their survival needs change over time.

![Survival Island after 120 ticks with the reference policy](examples/survival_island/screenshot.png)

### How it works

The application owns the simulation loop. On each tick it updates needs, asks a
policy for one action per living survivor, and executes that action through a
per-agent `bbagents.ToolRegistry`. `FunctionTool` binds each tool to its survivor;
world methods enforce resource costs, valid locations, and inventory requirements.
Tools return `Observation` objects, and `InMemoryTrace` records decisions,
replanning, execution results, failures, and communicated knowledge.

The reference policy constructs persistent `Plan` objects with dependent
`PlanStep` objects. An urgent need interrupts the current plan: a thirsty survivor
locates water, travels there, and drinks before resuming its personal objective.
Agents share river coordinates through messages. Movement uses pathfinding;
stationary observation restores energy, shelters improve rest, and forest berries
replenish each day. Hunger and thirst increase toward 100; energy decreases toward
zero. Dangerous levels damage health.

An optional local LLM policy sends each survivor's needs, inventory, remembered
map, resources, messages, previous proposed plan, and latest action result to an
Ollama-compatible server. The model returns a goal, a short plan, and one tool
call constrained by a tool-specific JSON schema. `ContextBuilder` budgets each
snapshot; `DecisionRuntime` validates proposals, retries a failed decision once,
and traces attempts. A single background worker serves agents in turn. Pending
agents observe/rest; urgent needs invoke the reference policy. Rendering continues.
The world validates actions before execution. Failed requests expose the actual
error in the event log and terminal. The sidebar counts accepted model proposals
and failed requests; acceptance does not guarantee world execution.

**Current limitation:** model plans are advisory and displayed in the sidebar;
they are revised on subsequent model responses rather than executed as persistent
action sequences. Rule-policy actions still run between responses. This is a
hybrid prototype, not a simulation wholly controlled by an LLM. Voice, weather,
seasons, disease, and predators are future additions.

### Run locally

From a checkout of this repository, using Python 3.11 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[game]'
python -m examples.survival_island.app
```

On Windows, activate with `.venv\Scripts\activate` instead. No model or API key is
needed for the reference policy.

Controls: **Space** pauses/resumes, **Right arrow** advances a tick while paused,
and **+ / -** changes simulation speed. The sidebar shows needs, inventory, plans,
recent actions, and events. The observer sees the full map; agents retain their
own limited knowledge.

To enable model decisions, start your Ollama server, download a model, and run:

```sh
ollama pull qwen3.5:9b
python -m examples.survival_island.app --model qwen3.5:9b
```

For a remote server:

```sh
python -m examples.survival_island.app --model qwen3.5:9b \
  --endpoint http://YOUR_SERVER:11434/api/chat
```

Local inference has no per-request API charges. Model downloads are explicit.
Start at 1–2 ticks per second to give the model time to deliberate as needs evolve.
Requests time out after 90 seconds; there is no tick-based expiry. Five survivors
share one model on the server. Use a model actually installed on that server
(`ollama list`), for example `--model qwen3.5:4b`.
`model.decision` and `model.failed` trace events distinguish model responses from
reference-policy actions.

The simulation also runs without pygame or a display:

```python
from examples.survival_island import Simulation

simulation = Simulation(seed=7)
for _ in range(1000):
    simulation.step()
print([(agent.name, agent.alive) for agent in simulation.agents])
```

Implementation: [simulation](examples/survival_island/simulation.py),
[model policy](examples/survival_island/model_policy.py),
[renderer](examples/survival_island/app.py), and
[tests](tests/test_survival_island.py).

## Features

The previous README's approval/recovery example is preserved as
[recoverable_release.py](examples/recoverable_release.py).

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

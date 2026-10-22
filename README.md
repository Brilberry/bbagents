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

Each survivor repeats the same loop: observe what it knows, choose an action,
try it in the world, and use the result to decide what to do next.

```mermaid
flowchart LR
    O["Observe<br/>Needs, inventory, visible terrain,<br/>remembered resources and messages"]
    D["Decide<br/>Rule policy or local LLM<br/>Choose one available action"]
    E["Execute<br/>Check the current world<br/>Run the tool if valid"]
    F["Feedback<br/>Result or rejection<br/>Update the agent's knowledge"]
    O -->|ContextBuilder| D
    D -->|Validated ToolCall| E
    E -->|Observation| F
    F -->|Next decision| O
```

For example, Ari needs shelter. She remembers a forest, moves there, and gathers
wood. If she becomes thirsty, she can interrupt that work to find water. If another
survivor has taken the last wood, the gather action is rejected; that feedback
helps her choose a different action next time. Survivors can also tell each other
where they found a river.

`bbagents` supplies the building blocks behind this loop:

- **Observation:** `ContextBuilder` assembles a bounded view of the survivor's
  situation, recent results, and available actions for the model.
- **Decision:** `DecisionRuntime` validates model output and action preconditions,
  retries a failed decision once, and records attempts. The rule policy instead
  follows a `Plan` made of dependent `PlanStep` objects.
- **Execution:** `ToolRegistry` exposes actions such as `move`, `gather`, and
  `build`. The game checks their preconditions again before changing the world.
- **Feedback:** tools return `Observation` objects. Results and failures feed the
  next model request; observed depletion updates the survivor's resource memory.
  `InMemoryTrace` records the model decisions and tool executions for inspection.

You can run entirely with the rule policy or enable a local LLM through Ollama.
Model requests run in the background while the game continues. Waiting agents
observe without gaining energy; fatigue, urgent needs and failed requests use the
rule policy. The sidebar
shows model proposal counts and errors, with full errors in the terminal.

**Current limitation:** the LLM proposes one action per response. Its longer plan
is displayed and reconsidered on later responses, but is not automatically
executed as a sequence. Voice, weather, seasons, and disease are future
additions.

### Predators and defense

The first wolf pack arrives at tick 250, with another arrival every 250 ticks:
a pack of **2–3 randomly chosen wolves**, another wolf pack, then one lone bear,
repeating. Defeated animals stay defeated; arrivals do not replace losses.
Wolves act every four ticks; bears act every two ticks. Agents only
observe predators within their visibility range.

Wolves travel together and share a target when any pack member detects a survivor.
Each wolf deals only **3 damage** per attack (9 if all three land a hit). Bears
hunt independently and deal **30 damage** per attack, with **150 health**.
When members of different wolf packs meet on the same or adjacent tiles, both
packs merge, including distant packmates, and share a target. Merges can create
packs larger than three. Bears never join packs.

Survivors can `craft("spear")` using three wood, `attack(predator_id)` when
adjacent, `flee()` two tiles at an energy cost, or `rest()` when no predator is
visible. A spear deals 25 damage, an axe 12, and bare hands 5. Shelters reduce
incoming damage by 75%. Rest restores energy and slowly heals fed, hydrated agents.
Attacks cost 4 energy and have a three-tick cooldown, so a survivor cannot strike
repeatedly before a bear responds. The rule policy prefers fleeing when outnumbered,
injured, or facing a bear without two nearby allies.
Energy falls each tick and with activity: moving costs 1.2, gathering or crafting
costs 2, and building costs 6. Observation never restores energy. Explicit rest
restores 3 outdoors or 6 in a shelter. The rule policy interrupts work at 30 energy
and rests toward 70 before resuming, including while waiting for a model response.
The rule policy prepares spears when it has wood from tick 200, attacks adjacent
threats with a spear, and otherwise tries to flee. Immediate defense also overrides
pending model requests so inference latency does not prevent a response. Model
actions use the same tools, availability checks, observations, and traces.

Use `Simulation(predator_interval=None)` to disable predators for baseline
experiments, or set a different positive interval.

### New arrivals, shelter reuse, and lightning

Every **1,000 ticks**, another survivor spawns at a random walkable land tile on
the island's edge. New arrivals have their own identity, tools, limited knowledge,
plans, and memory. They begin by looking for shelter. Scroll the sidebar with the
mouse wheel or Page Up / Page Down to inspect more than five survivors.

The `claim_shelter()` tool lets a survivor take an unassigned shelter, one whose
owner died, or an older shelter its owner no longer calls home. It costs no wood;
an actively owned home cannot be taken. Builders reuse their existing home rather
than building a new shelter after each interruption.

**Lightning** has a 0.4% chance each tick (one strike per 250 ticks on average,
not a fixed schedule). When shelters exist, half of strikes target a shelter;
the rest hit a random tile. A struck shelter is destroyed, its home assignment
is cleared, and sleepers on that tile wake immediately. Lightning does not directly
damage health. A yellow bolt briefly marks the strike, and observations and traces
record the calamity. Its randomness is seeded for repeatable experiments.

For isolated experiments, `survivor_interval=None` disables new arrivals and
`lightning_probability=0` disables lightning.

### Sleep, interruption, and resuming work

From tick 44 of each 80-tick day, the rule policy pauses the survivor's current
plan and prepares for night: eat if hungry, drink if thirsty, reach a known safe
shelter if one is available, wait for night, then sleep. The same preparation
policy runs when a local model is enabled. If preparation becomes unsafe, survival
takes priority and the agent can skip that night's sleep.

```mermaid
flowchart LR
    Work[Current plan] --> Save[Checkpoint plan]
    Save --> Prep[Eat, drink, reach shelter]
    Prep --> Sleep[Sleep across 12 ticks]
    Sleep -->|Finished| Resume[Resume saved plan]
    Sleep -->|Predator or urgent need| Respond[Wake and respond]
    Respond -->|Safe again| Resume
```

The `sleep()` tool starts the activity once. The simulation advances it each tick
without selecting more actions, restoring 2.5 energy outdoors or 5 in shelter
before passive fatigue. Predators or hunger/thirst reaching 80 wake the agent
early; a predator attack also interrupts sleep immediately. Sleeping survivors
show a **z** on the map and a countdown in the sidebar.

This adds three framework demonstrations to the basic decision loop:

- **Versioned checkpoints:** `InMemoryStateStore` saves the suspended plan,
  including completed steps and their evidence, plus sleep status and progress.
  Each update supplies the expected version. Once urgent needs and danger are
  resolved, the survivor restores that plan instead of starting its objective over.
- **Episodic memory:** `MemoryStore` records where sleep completed or was interrupted.
  Night preparation consults those memories to avoid shelters associated with
  predator interruptions; recent sleep memories also enter model requests.
- **Lifecycle tracing:** `plan.suspended`, `sleep.started`, `sleep.progress`,
  `sleep.interrupted` / `sleep.completed`, and `plan.resumed` expose the transitions.

Sleep scheduling and interruption are game-owned tick logic, not framework async
task cancellation. Checkpoints and memories are in memory for this run; this
example does not yet restore a saved island after restarting the process. Model
plans remain advisory; the checkpoint preserves the executable rule-policy plan.

### Traces and survivor postmortems

Close the window, press **Escape**, or interrupt with Ctrl+C to finish a run and
save its reports. Reports are also saved when a survivor dies; press **R** to save
an intermediate snapshot. The terminal prints the path to `index.md`, which links
to each survivor's history. By default, every run gets a unique directory under
`survival-runs/` (ignored by Git).

```sh
python -m examples.survival_island.app --model qwen3.5:4b \
  --report-dir survival-runs/my-experiment
```

Each directory contains:

- `index.md`: all survivors, their status, death tick, and recorded final cause.
- `agent-N.md`: that survivor's final needs and events from the last 200 ticks
  before death (or before the save for living survivors).
- `agent-N.jsonl`: the survivor's full event history, including received messages.
- `trace.jsonl`: the full run's framework trace, with simulation ticks, timestamps,
  and run/span correlation IDs.
- `agent-N.summary.md`: an optional model postmortem for a deceased survivor.

The trace includes per-tick health, hunger, thirst, energy, inventory, location,
sleep, visible threats, plans, tool calls and results, model failures, communications,
damage, and deaths. Tool calls identify rule decisions, model decisions, and
waiting for inference. A fatal predator attack is recorded separately from
starvation, dehydration, or exhaustion damage, making the final cause inspectable.

With `--model`, a death automatically queues a background analysis using the same
Ollama model. It receives that survivor's events from the final 200 simulation ticks,
ending at the death tick, even if the game continues afterward. `ContextBuilder`
limits the input to 60,000 characters, prioritizing damage and death evidence;
the report states how many items were omitted if the window exceeds this budget.
`DecisionRuntime` validates the summary and requires evidence ticks to exist in
the supplied context. The explanation distinguishes the recorded cause, likely
contributors, and uncertainties; it is an interpretation, not proof of causation.

Raw reports are saved before model analysis. The window stays responsive while
summaries run. On exit the terminal may remain busy finishing queued analyses;
each request has a 90-second timeout and one retry. Model errors produce an error
report without discarding the raw history. Use `--no-postmortem` to keep model
gameplay enabled but save only the event reports. Without `--model`, event reports
are still generated and no summaries are requested.

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

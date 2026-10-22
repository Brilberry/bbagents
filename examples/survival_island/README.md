# Survival Island

A deterministic 30×30 agent simulation demonstrating long-running plans,
replanning, bounded tools, partial observability, communication, and conflicting
survival needs. Five autonomous survivors explore a generated island, find water
and food, gather materials, craft tools, and build shelters.

The included policy is deliberately deterministic and explainable. It can later be
replaced with a model-backed decision policy without changing the world tools or
letting model output mutate simulation state directly.

From the repository root:

```sh
python -m pip install -e '.[game]'
python -m examples.survival_island.app
```

Controls:

- `Space`: pause/resume
- `Right arrow`: advance one tick while paused
- `+` / `-`: simulation speed

The headless simulation does not require pygame:

```python
from examples.survival_island import Simulation

simulation = Simulation(seed=7)
for _ in range(200):
    simulation.step()
```

### Model policy

With an Ollama server already running and a model already downloaded:

```sh
python -m examples.survival_island.app --model YOUR_INSTALLED_MODEL
```

Use `--endpoint URL` for another Ollama-compatible `/api/chat` endpoint.
No provider SDK or API key is required for a local server. A model is not
downloaded automatically. Requests have a 90-second timeout and run on one
background worker so rendering continues. One request per agent is allowed in
flight. Pending agents observe without gaining energy; fatigue, urgent needs and failures use the reference
policy. Use `ollama list` to choose an installed model, such as `qwen3.5:4b`.

The model proposes a goal, a short plan, and one action. Proposed plans appear in
the sidebar; each subsequent model response revises that proposal. These are
advisory plans, not automatically executed action sequences. The reference policy
retains its own executable plan. Model responses pass tool-name and argument
validation, then the world applies its ordinary resource and location checks.
`ContextBuilder` budgets snapshots. `DecisionRuntime` validates a tool-specific
schema, retries a failed response once, and records standard decision trace events.
There is no tick-based response expiry. The UI counts accepted proposals and
failures; per-agent status and the terminal expose errors. Trace events `model.decision` and
`model.failed` distinguish actual model responses from fallback actions.

Energy recovery requires the explicit `rest()` tool: 3 outdoors or 6 in a shelter.
Observation never restores energy. Moving costs 1.2, gathering/crafting 2, and
building 6, in addition to passive fatigue. The rule policy rests at 30 energy
until reaching 70, then resumes work. Requests include explicit legal actions and current-tile
resources. Framework validation rejects choices unavailable in the snapshot, and
the main thread checks preconditions again against the live world before acceptance.
The last eight observations include tool results, failures, and rejected actions
so the next model request can correct mistakes. Observing depletion removes the
resource location from personal memory.
The most recent failure is retained separately from the recent-observation buffer.

The baseline replenishes
forest berries each day. This makes sustained survival possible. Disease and
voice are not implemented yet; lightning is the first weather calamity.

Predators arrive starting at tick 250, with another arrival every 250 ticks:
a pack of 2–3 random wolves, another pack, then a lone bear, repeating.
Wolves stay together and share prey within each pack, dealing 3 damage per wolf.
Bears have 150 health, hunt independently, and deal 30 damage. Wolves act every
four ticks and bears every two. Touching wolf packs merge into a larger pack.
Survivor attacks cost 4 energy and have a three-tick cooldown. Predators are visible to agents only
at close range. Survivors can craft a spear from three wood, attack adjacent
predators, flee two tiles, or rest to recover when safe. Shelters reduce attacks
by 75%. Immediate rule-based defense overrides model latency. Wolves and bears
are marked W and B on the map; combat appears in events and traces.
Use `Simulation(predator_interval=None)` for a predator-free baseline.

Every 1,000 ticks a new survivor arrives on a random land tile at the map edge.
`claim_shelter()` allows free reuse of unassigned/abandoned homes; builders keep
using a valid existing home. Scroll the sidebar or use Page Up / Page Down to
inspect arrivals. `survivor_interval=None` disables this population growth.

Lightning has a seeded 0.4% chance per tick. Half of strikes target an existing
shelter when possible; others hit random tiles. Strikes destroy shelters, clear
home assignments, and interrupt sleep, but cause no direct health damage.
A yellow bolt marks the latest strike. Set `lightning_probability=0` to disable it.

### Sleeping and saved plans

At dusk (tick 44 of each 80-tick cycle), survivors pause their current plan,
eat/drink as needed, choose a known safe shelter if possible, and wait for night.
`sleep()` begins a 12-tick activity. Sleeping stops normal action selection;
the map shows a z and the sidebar shows remaining ticks. Recovery is 2.5 energy
outdoors or 5 in shelter per tick, less passive fatigue. Visible predators,
attacks, or hunger/thirst reaching 80 interrupt sleep. Defense and urgent needs
take priority; the saved plan resumes when safe.

`InMemoryStateStore` checkpoints the original `Plan`, step statuses/evidence, and
sleep progress using expected versions. `MemoryStore` records sleep episodes;
preparation avoids shelters associated with predator interruptions. Those memories
also enter model context. Lifecycle trace events cover suspension, start, progress,
interruption/completion, and resumption. The application owns tick scheduling;
this is not framework async task cancellation or restart recovery of the world.

### Survivor histories and death summaries

Press R to save a snapshot; closing the window, Escape, or Ctrl+C saves the final
reports. Deaths also trigger a save. The terminal prints the `index.md` path under
a unique `survival-runs/` directory. Use `--report-dir PATH` for an explicit location.
The index links to each survivor's Markdown report and full JSONL history;
`trace.jsonl` preserves the complete run with ticks and framework correlation IDs.
Reports show per-tick needs, inventory, location, actions, plans, threats, messages,
damage, death tick, and recorded cause. No model is needed for raw reports.

With `--model`, deceased survivors also get `agent-N.summary.md`: a background
analysis of their final 200 ticks. `ContextBuilder` bounds the model input to
60,000 characters and reports omissions; `DecisionRuntime` validates the result
and its evidence ticks. Summaries separate the recorded cause from possible
contributors and uncertainty. Failed analysis leaves an error report and preserves
the raw logs. Exit waits for queued summaries (90-second timeout, one retry per
request). Use `--no-postmortem` to disable this additional inference.

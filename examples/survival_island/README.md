# Survival Island

A deterministic 30×30 agent simulation demonstrating long-running plans,
replanning, bounded tools, partial observability, communication, and conflicting
survival needs. Five autonomous survivors explore a generated island, find water
and food, gather materials, craft tools, and build shelters. The central river is
retained, with three additional separate lakes
are spread across different map regions, with seeded sizes/positions and walkable
shorelines. Survivors can drink from a lake or an adjacent tile and share lake
locations, giving them alternatives to relying only on the central river. River
and lake water both work for drinking, communication, and predator water seeking.

Survivors start apart as independent generalists with no knowledge of each other,
not fixed team roles. Their priorities change with personal discoveries and needs:
water, shelter, food reserves, personal defenses/tools, then exploration. All
survivors can perform all tasks. New arrivals follow the same adaptive policy.
Encounters create episodic memories. Messages require a discovered survivor who
is currently within visibility; finding water does not broadcast to the population.
Seeded generosity, cooperation, and caution control individual social policies:
ask for information, offer it for free or at a price, trade supplies, or invite a
construction partner. Model context contains encountered survivors only; changed
objectives and social offers still respect inference tick/time limits. Objective
changes, encounters, negotiations, and work contributions are traced.

### Social tools and shared projects

- `communicate(id, {"kind": "request_info", "topic": "water"})` asks a local contact.
  Topics are `water`, `food`, `wood`, and `shelter`. A policy can reply with
  `{"kind": "information", "topic": "water"}`, refuse with `decline`, or quote
  `{"kind": "quote_info", "topic": "water", "price": {"berries": 1}}`.
- `trade(id, offer={"berries": 2}, request={"water": 1})` proposes a barter.
- `collaborate(id, project={"position": [x, y]})` invites shared construction.
- `respond_offer(offer_id, accept=True/False)` provides consent; `cancel_offer(id)`
  withdraws the proposer's offer. Offers expire after 32 ticks (maximum four pending
  per survivor). Both sides must be awake, alive, discovered, and nearby at acceptance.
- `work_project(id)` contributes wood and up to one construction work action per
  tick at the site. `leave_project(id)` leaves resources/work in place.

Exchange validates both inventories before applying either side, then closes the
offer exactly once. Inventories are not reserved while waiting. Paid information
is hidden from the buyer until payment and disclosure succeed together. Information
is drawn from the provider's memory, not invented; it can be stale. Plain chat is
untrusted data and never executes tools or binds another survivor to a proposal.
Policies may voluntarily send `{"kind": "status"}`; other inventories remain private.

Crafting a `flask` costs two wood. `collect_water()` fills it at a water source;
the resulting `water` item includes its flask and can be traded. `use_water()`
reduces thirst by 50 and returns the empty flask.

A solo shelter requires 10 wood and four work actions (6 construction energy,
one bed). A shared shelter requires 14 wood and six actions (9 energy total,
two beds). With materials ready, two builders finish in three ticks, spending
4.5 energy each, rather than four solo ticks and 6 energy each for separate homes.
Both partners become residents; one partner's death does not evict the other.
Projects survive departures/deaths, can admit replacement partners, and abandoned
projects can be adopted with `build("shelter")`. Policies can leave after
96 + cooperation ticks without progress. Urgent needs and sleep pause work.

Model responses may contain an optional `social: {tool, arguments}` action, which
becomes a `PlanStep` and uses the same world validation and consent rules. It is
not cached and uses ordinary planning requests, not an extra call per interaction.
New offers may trigger replanning within the existing limits. Reference policies keep
social decisions working while inference is throttled, and take over pending
consent with eight ticks left before expiry if inference is slow. Hover over survivors for
traits/offers and shelters for beds; gold outlines mark unfinished projects.

Framework building blocks: `ToolRegistry`, policy-checked `ActionRuntime`,
`AuthorizationDecision`, typed organizational messages, `Plan`/`PlanStep`, versioned
`InMemoryStateStore`, `MemoryStore`/`Provenance`, `DecisionRuntime`, `ContextBuilder`,
and trace sinks. The game-specific offer/transaction protocol lives in `social.py`;
bbagents has no generic multi-party negotiation/transaction primitive yet. These
are serial in-memory transactions, not durable/distributed game-state recovery.

Run with the deterministic reference policy, or let a local model propose
persistent milestone plans. Both execute through the same world tools.

From the repository root:

```sh
python -m pip install -e '.[game]'
python -m examples.survival_island.app
```

Controls:

- `Space`: pause/resume
- `Right arrow`: advance one tick while paused
- `+` / `-`: simulation speed

The run ends when the last survivor dies. Ticks and new arrivals stop; the window
stays open on the final state with “Game over”. Space/single-step cannot resume a
finished run. Reports save automatically, background postmortems finish, and final
logs are flushed. Headless `Simulation.step()` also becomes a no-op after game over.

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
background worker so rendering continues. At most one gameplay request is in flight
across all agents. The reference policy keeps survivors active while planning is
pending and handles urgent survival. Use `ollama list` to select an installed model.

The model returns a goal and 1–6 milestones such as `gather_wood`, `build_shelter`,
and `rest`. These become a framework `Plan` with dependent `PlanStep` objects.
Local controllers execute movements, repeated gathering, crafting, and other tools
until each milestone completes, keeping evidence of progress. `DecisionRuntime`
validates the milestone schema and traces failed attempts. Every actual tool call
still passes live availability and world checks. The UI shows plans, cache hits,
and failures; traces distinguish model plans from reference-policy actions.

Only plan completion or significant changes request replanning. Seeded intelligence
(0–100) sets each survivor's minimum waits: 50–75 ticks (±2 jitter, clamped to that
range) and 20–40 seconds, with smarter survivors eligible more often. Both limits
must expire after the previous result, including failed requests. The starting
group includes a smart survivor (85–100) and a less intelligent one (5–20).
Initial eligibility is staggered by 5–10 ticks per survivor, including new arrivals.
Gameplay failures wait for the next window instead of immediately retrying.
Intelligence is displayed in the sidebar and saved reports; local emergency
actions remain immediate. A shared inference gate enforces a three-second
idle gap after each call, including retries and postmortems. Increase that gap
using `--inference-cooldown 6`. Routine requests use 8,192 context tokens and an
8,000-character snapshot budget. A bounded framework `Cache` reuses matching
strategy proposals; changing relevant state changes the key. Immediate survival
interrupts preserve model-plan progress. A milestone stalled for 80 actions fails
and requests replanning rather than running forever.

Energy recovery requires the explicit `rest()` tool: 3 outdoors or 6 in a shelter.
Observation never restores energy. Moving costs 1.2, gathering/crafting 2, and
construction work 1.5 per action, in addition to passive fatigue. The rule policy rests at 30 energy
until reaching 70, then resumes work. Local controllers choose legal actions from
the current world. Observing depletion removes the resource location from memory.
The most recent failure is retained separately from the recent-observation buffer.

The baseline replenishes
forest berries each day. This makes sustained survival possible. Disease and
voice are not implemented yet; lightning is the first weather calamity.

Predators arrive starting at tick 250, with another arrival every 250 ticks:
a pack of 2–3 random wolves, another pack, then a lone bear, repeating.
Wolves have 45 health, stay together, and share prey within each pack, dealing 7 damage per wolf.
Bears have 150 health, hunt independently, and deal 30 damage. Wolves act every
four ticks. Bears move every two ticks but attack only once every six ticks.
Touching wolf packs merge into a larger pack.
Survivor attacks cost 4 energy and have a three-tick cooldown. Predators are visible to agents only
at close range. Survivors can craft a spear from three wood, attack adjacent
predators, flee two tiles, or rest to recover when safe. Shelters reduce attacks
by 75%. Immediate rule-based defense overrides model latency. Wolves and bears
are marked W and B on the map; combat appears in events and traces.
Killing a wolf reduces the killer's hunger by 30 and thirst by 10; killing a bear
reduces them by 50 and 20. This automatic food/hydration reward is applied only
once, on the killing blow, and clamps needs at zero. Nonfatal attacks grant no
reward. Tool feedback and `survivor.kill_reward` events record the actual recovery.
Use `Simulation(predator_interval=None)` for a predator-free baseline.

Predator hunger controls hunting range: below 40 is calm, then 2/6/10 tiles at
40/60/85. Kills reduce the killer's hunger by 65 and nearby packmates' by 45;
nonfatal hits do not feed them. Calm animals defend themselves for 12 ticks if
attacked. Survivors distinguish calm animals from threats, so can rest or sleep
near well-fed predators. Each arrival has varied hunger (45–75) and thirst (10–35).
At 55 thirst predators seek a reachable riverbank or lake shore; drinking reduces thirst by 65.
At 85 thirst water takes priority even over adjacent prey. A shared breadth-first
distance field routes them around mountains without entering water, without LLM
calls. They continue meeting needs when no survivors remain.
Needs rise by 0.025 hunger/0.04 thirst per tick, versus survivors' 0.55/0.8.
At 100 they take 0.08 starvation/0.12 dehydration damage per tick, giving them
much longer endurance. Hover over an animal for HP, hunger, thirst, and behavior.
Predator needs, feeding, drinking, and deaths are included in traces and visible
predator details in survivor timelines.

Every 1,000 ticks a new survivor arrives on a random land tile at the map edge.
`claim_shelter()` allows free reuse of unassigned/abandoned homes; builders keep
using a valid existing home. Scroll the sidebar or use Page Up / Page Down to
inspect arrivals. `survivor_interval=None` disables this population growth.

Lightning has a seeded 0.4% chance per tick. Every map tile is equally likely
to be struck, with no bias toward shelters or terrain. Strikes destroy shelters, clear
home assignments, and interrupt sleep, but cause no direct health damage.
A yellow bolt marks the latest strike. Set `lightning_probability=0` to disable it.

### Sleeping and saved plans

At dusk (tick 44 of each 80-tick cycle), survivors pause their current plan,
eat/drink as needed, choose a known safe shelter if possible, and wait for night.
`sleep()` begins a 12-tick activity. Sleeping stops normal action selection;
the map shows a z and the sidebar shows remaining ticks. Each tick restores
2.5 energy and 1 HP outdoors, or 5 energy and 2 HP in shelter, capped at 100.
Hunger, thirst, passive fatigue, and need damage pause while asleep. A shelter
has one bed when built solo or two when built jointly, with one sleeper per bed;
preparation avoids full shelters and waking
or death releases capacity. Outdoor sleeping has no occupancy limit. Visible predators,
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
Events stream to disk; RAM retains the last 200 ticks, capped at 30,000 events.
Older histories and final death windows remain readable from disk. Reusing a
report directory starts a new subdirectory so histories from runs do not mix.
Reports show per-tick needs, inventory, location, actions, plans, threats, messages,
damage, death tick, and recorded cause. No model is needed for raw reports.

With `--model`, each deceased survivor's `agent-N.md` also includes a background
analysis of their final 200 ticks, above the event timeline. No separate summary
Markdown file is created. `ContextBuilder` bounds the model input to
60,000 characters and reports omissions; `DecisionRuntime` validates the result
and its evidence ticks. Summaries separate the recorded cause from possible
contributors and uncertainty. Failed analysis leaves an error report and preserves
the raw logs. Exit waits for queued summaries (90-second timeout, one retry per
request). Use `--no-postmortem` to disable this additional inference.

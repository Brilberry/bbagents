# Survival Island

Start with the [overview and run instructions](OVERVIEW.md) for the observation →
decision → execution → feedback loop, setup, controls, and reporting.

For the current skill catalog, finite fisheries, vegetable plots, fire/cooking,
leather clothing, and shelter reservoirs, see [Survival systems](SURVIVAL_SYSTEMS.md).

## Seasons, weather, and temperature

The island uses a northern-hemisphere temperate climate. **Spring is the default
and gentlest starting season**: mild temperatures, no snow or heatwaves, and
occasional rain. Seasons advance **spring → summer → autumn → winter**, lasting
10 simulation days each (800 ticks; a year is 3,200 ticks). Weather changes daily;
nights are 5°C cooler. The sidebar, observations, model context, and reports show
the current season, weather, and temperature.

| Season | Sunny daytime base | Rain probability per day | Other conditions |
| --- | --- | --- | --- |
| Spring | 16–23°C | 25% | Mild; easiest start |
| Summer | 24–31°C | 20% | 12% of sunny days are 36–40°C heatwaves |
| Autumn | 9–18°C | 40% | Colder, wetter nights |
| Winter | −5–7°C | 25% | Additional 20% chance of snow, at or below 0°C |

Rain cools the air by 3°C. Rainy days can be thunderstorms (40% of summer rain,
10% in other seasons). The lightning setting is a base probability: normal rain
uses 1×, thunderstorms 3×, and other weather 0.25×. Targets remain uniform across
the map. Climate schedules are seeded separately from survivor choices and other
environmental random streams; all competitors receive the same conditions.

Above 28°C, awake thirst rises faster: baseline drain is multiplied by
`1 + 0.06 × (temperature - 28)`. Shelter reduces the extra heat effect by 75%.
For cold exposure, shelters add 15°C effective protection; rain outdoors adds a
3°C wetness penalty. Below effective 5°C, exposure rises by `(5 - effective)/20`
per tick; otherwise it falls by 0.5. Exposure at 40 or more causes hypothermia
damage, increasing up to 1 HP per tick at exposure 100, and can be fatal.
Cold damage continues while sleeping, and sleep/rest cannot heal HP until
exposure falls below 40. Sleep still freezes hunger and thirst.

During freezing conditions, snow, heat, or thunderstorms, survivors prefer a
remembered, permitted shelter and rest there. Immediate danger, urgent food/water
needs, and interrupted sleep still take precedence. If no usable shelter is
known, normal survival/building plans continue. Fires and equipped leather
garments add warmth; crops respond to weather and season. Weather currently does
not freeze lakes or alter wildlife physiology.

For normal play, optionally pass `--starting-season winter`. Competitions set
`"starting_season": "spring"` in their JSON, with allowed values `spring`, `summer`,
`autumn`, `winter`; omitted values default to spring. CLI overrides are rejected
for competitions. Starting in spring is gentler, not a guarantee of a higher score
on every seed. All current map presets share this climate profile.

Solo shelters are private: only their living owner may sleep there, even while
the owner is away. Shared shelters allow visitors when a bed is free. An abandoned
shelter must be claimed before sleeping in it; visiting never transfers ownership.

Press **H** for a modal keyboard-shortcut reference. Help pauses simulation ticks
and blocks gameplay shortcuts while open. Close it with **H**, **Escape**, or the
**Close** button; the previous paused/running state is restored. It also works
over the Game Over dialog. Window close and terminal Ctrl+C still exit normally.

Future feature ideas and dependencies are tracked in the [TODO backlog](TODO.md).

For player-created teams, separate competition settings, all supported config
values, scoring, and reproducible headless runs, see
[Teams and competitions](CONFIGURATION.md). Example files live in `configs/`.

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
survivors can perform all tasks. No new survivors arrive during a run.
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

The run ends when the last survivor dies. Ticks stop; the window
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
Initial eligibility is staggered by 5–10 ticks per survivor.
Gameplay failures wait for the next window instead of immediately retrying.
Intelligence is displayed in the sidebar and saved reports; local emergency
actions remain immediate. A shared inference gate enforces a three-second
idle gap after each call, including retries and postmortems. Increase that gap
using `--inference-cooldown 6`. Routine requests use 8,192 context tokens and an
14,000-character snapshot budget. A bounded framework `Cache` reuses matching
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
predators, flee two tiles, or rest to recover when safe. Intact shelters block
all animal entry and attacks, including boar retaliation. Sheltered survivors can
sleep safely despite nearby predators. Leaving the shelter or its destruction
removes this protection. Immediate rule-based defense overrides model latency. Wolves and bears
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

No new survivors arrive during a run.

When a survivor dies, its remaining inventory is deposited at its valid home,
even if it died elsewhere. Claiming that abandoned shelter transfers the stored
resources and tools exactly once; learned skills are not inherited. A shared
shelter stays occupied while any living resident still calls it home. That
resident can claim the stored estate without displacing other living residents.
If every resident dies, another survivor can claim the shelter and its contents.
Lightning destroys stored contents with the shelter. Deaths without a valid home
do not create a shelter stash. Estate deposits and inheritance appear in traces
and the single-file survival report. Living survivors still use their existing
inventory directly; this change does not introduce remote deposit/withdraw tools.
`claim_shelter()` allows free reuse of unassigned/abandoned homes; builders keep
using a valid existing home. Scroll the sidebar or use Page Up / Page Down to
inspect larger configured teams.

Lightning has a seeded 0.4% base chance per tick, modified by weather as described
above. Every map tile is equally likely
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

When all survivors die, a **Game Over** dialog offers **Generate postmortem**
(or press Enter) and **Exit**. Generation waits for any model summaries, preserves
`survival-report.md`, then renders `survival-report.html` beside it and opens the
HTML in your default browser. The window remains responsive while generation
runs; repeated clicks do not create duplicate jobs. The button becomes **Open
postmortem** afterward. If generation fails, Markdown remains intact and a retry
is offered. If no browser is available, the terminal prints the saved HTML path.
Without a model, or with `--no-postmortem`, the same button exports the recorded
facts and timelines without requesting model analysis. No external rendering
service, JavaScript, or additional dependency is required. Event logs are
expandable in HTML, and untrusted messages/model output are escaped as text.

Press R to save a snapshot; closing the window, Escape, or Ctrl+C saves the final
report. Deaths also trigger a save. The terminal prints the `survival-report.md` path under
a unique `survival-runs/` directory. Use `--report-dir PATH` for an explicit location.
One self-contained file includes the overview, contents links, all survivor
details and death windows, model postmortems, and the complete run's JSONL history
with ticks and framework correlation IDs. No separate report or log files are
exported by default; the Game Over button adds the optional HTML view of that
same report. Events stream to temporary internal disk journals; RAM retains the
last 200 ticks, capped at 30,000 events. Exports stream into an atomic replacement.
Older histories and final death windows remain readable from disk. Reusing a
report directory starts a new subdirectory so histories from runs do not mix.
Reports show per-tick needs, inventory, location, actions, plans, threats, messages,
damage, death tick, and recorded cause. No model is needed for raw reports.

With `--model`, each deceased survivor's section also includes a background
analysis of their final 200 ticks, above the event timeline. No separate summary
Markdown file is created. `ContextBuilder` bounds the model input to
60,000 characters and reports omissions; `DecisionRuntime` validates the result
and its evidence ticks. Summaries separate the recorded cause from possible
contributors and uncertainty. Failed analysis leaves an error report and preserves
the raw logs. Exit waits for queued summaries (90-second timeout, one retry per
request). Use `--no-postmortem` to disable this additional inference.
# Water crossings

Bears can cross up to **three consecutive water tiles**; wolves, deer, and boars
up to **one**; rabbits cannot enter water. Before entering, an animal must have
a route to a different dry tile within its crossing limit. It follows that route
until it reaches land, so waiting or drinking does not reset its crossing budget.
These rules apply to lakes and rivers during roaming, hunting, fleeing, and
water seeking. All animals can drink on a tile adjacent to water (Manhattan
distance one); swimmers can also drink from within water. Wildlife thirst rises
by 0.06 per tick and prompts water seeking at 55, urgent at 85. Drinking removes
65 thirst. These are game-specific movement rules.

Survivors can cross rivers and lakes. Entering water and stepping onto land take
one tick each. Moving between consecutive water tiles takes three ticks per tile:
one movement tick followed by two swimming recovery ticks. Single-tile river
crossings therefore stay quick, while traversing a lake is slower. Needs and
predators continue updating during recovery, and swimming waits appear in traces.

## Learning to fish

Fishing, axes, and spears are learned crafting skills; flasks and rations remain
starter knowledge. After seeing a lake, a survivor discovers fishing at personal age
`700 - 5 × intelligence` ticks (200–700 ticks, using internal 0–100 intelligence).
Alternatively, a knowledgeable nearby survivor can teach it immediately through
the `fishing` information topic. Existing generosity policies decide whether to
answer freely, charge for the lesson, or decline. Plain chat cannot grant skills.

A learned survivor can craft a `fishing_rod` from four wood. The `fish()` tool
requires a rod, three energy, and a lake within one tile; it catches one fish with
an eight-tick cooldown. Fish reduce hunger by 50 and can be traded, as can rods.
Receiving a rod does not grant the skill. Fishing consumes finite, replenishing
lake fish stocks shared with bears; see the survival-systems guide for capacities.
Local and model-plan food controllers can pursue fishing when berries are not
already in inventory. Survival emergencies and predator responses retain priority.
Learning is recorded in bbagents memory, survivor state traces, and model context;
lessons use the same authorized information/offer flow as other knowledge trades.

## Wildlife and hunting

Every island starts with **five deer, four boars, and eight rabbits**, seeded
independently of survivor choices. They roam on land, seek nearby berries when
hungry, and consume the same finite stock as survivors. Deer and rabbits flee
nearby survivors and predators. There is no reproduction or replacement yet.

Bow discovery requires having seen wildlife, remembering wood and stone, and
personal age `400 - 2 × internal intelligence` (200–380 ticks for configured
intelligence 1–10). A local teacher can teach `bow` or `hunting` through the same
free/paid information flow as other skills. Crafting a bow costs **five wood and
one stone** and teaches basic hunting. Receiving equipment alone does not grant
knowledge; a survivor who knows bow-making can learn hunting by practicing with
an acquired bow. Axes, spears, and bare hands cannot invoke the hunting tool.

`hunt(animal_id)` requires learned hunting, a bow, four energy, visible living
prey within three Manhattan tiles, and a ready four-tick cooldown. A shot deals
`16 + 2 × strength` damage if it hits. Ammunition is abstracted in v1; there is
no separate arrow inventory. Outcomes and all food transfers are engine-validated.

| Animal | HP | Hit chance per shot | Meat portions on kill |
| --- | --- | --- | --- |
| Deer | 50 | 65% | 4 |
| Boar | 75 | 80% | 5 |
| Rabbit | 18 | 50% | 1 |

Raw meat restores **35 hunger** per portion and can be traded or inherited.
Wounded or missed boars are provoked for 12 ticks and may pursue their hunter,
dealing 10 adjacent damage every five ticks (intact shelters block attacks). Cautious
or injured survivors avoid hunting boars and retreat from provoked ones.

Reference food plans consider nearby wildlife when the survivor knows bow-making,
lacks carried berries/fish, and known berries are not closer. Fish-preferring
survivors retain a fishing option when practical. Model plans have `craft_bow`
and `hunt_food` tasks and receive only locally visible wildlife information.
The viewer marks deer **D**, boars **b**, and rabbits **r**. Wildlife movement,
feeding, shots, kills, retaliation, and resulting deaths appear in traces and
the single-file survival report. Postmortem instructions explain these mechanics.
Hungry bears (hunger at least 40, thirst below 85) opportunistically kill deer or
rabbits on the same or an adjacent tile, and may pursue nearby wildlife when no
human is in hunting range. Bears can also catch fish from shared lake stocks.
Deer reduce bear hunger by 60 and rabbits by 20. Feeding uses the bear's shared
six-tick attack cooldown, so it cannot also attack a survivor during that cooldown.
Calm bears leave wildlife alone; boars are not prey in this version. Prey deaths
and bear feeding are traced, and no survivor receives meat from a bear's kill.
Cooking is implemented; breeding remains future work.

## Axe and spear progression

Survivors do not follow a mandatory spear-then-axe progression. Spear discovery
requires remembered wood and personal age `180 - internal intelligence` ticks
(80–170 with configured intelligence 1–10). Axe discovery requires remembered
wood and stone and age `350 - 2 × internal intelligence` (150–330 ticks).
Internal intelligence is the configured value multiplied by ten. Local teachers
can supply either skill earlier through free or paid knowledge exchanges, just
like fishing. Having materials alone does not unlock a recipe.

The reference policy prepares a spear after recent danger or when a cautious
survivor anticipates predator arrivals. It considers an axe only after gathering
at least eight wood over its lifetime and when at least eight more wood are
needed for shelter construction. Already-equipped survivors do not keep crafting.
In configured teams, optional tool projects participate in `learn_skills` priority;
critical needs, sleep, and immediate danger retain precedence. Learning a recipe
does not oblige the survivor to use it.

Model plans support both `craft_spear` and `craft_axe`, with skill requirements
enforced before gathering materials. Context includes known skills and a
need-driven tool recommendation. The sidebar and state traces display the active
plan goal (including model plans), not an unrelated strategic objective.

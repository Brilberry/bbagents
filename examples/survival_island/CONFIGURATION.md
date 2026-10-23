# Teams and competitions

No additional player keys are needed for gardening, fire/cooking, leather clothing,
or reservoirs. Intelligence and existing priorities influence these learned
activities; costs, tools and prerequisites are defined in [Survival systems](SURVIVAL_SYSTEMS.md).

Install from the repository root:

```sh
pip install -e '.[island]'
```

Run the example team across every competition seed, printing a JSON results manifest:

```sh
python -m examples.survival_island.competition \
  --team examples/survival_island/configs/team.json5 \
  --competition examples/survival_island/configs/competition.json
```

Watch one seed in the game window:

```sh
python -m examples.survival_island.app \
  --team examples/survival_island/configs/team.json5 \
  --competition examples/survival_island/configs/competition.json --seed 17
```

The headless runner requires JSON5 but not pygame. Competitive v1 is rules-only:
`--model` cannot be combined with competition settings. Normal noncompetitive
model play remains available. Configurations are data, never executable code.

## Team JSON5: complete accepted keys and values

Unknown keys, duplicate keys, invalid types (including booleans where integers
are required), out-of-range settings, and incorrect budgets are rejected.
Files are limited to 64 KiB. No spawn slots: array order assigns survivors to
the same seeded starting positions, all at tick zero. Survivors still initially
know nothing about their teammates. Team size must match the competition.

| Key | Accepted values | Required/default |
| --- | --- | --- |
| `schema_version` | Integer `1` | Required |
| `team_name` | Nonblank string, at most 48 characters | Required |
| `survivors` | Array, length equal to `survivor_count` | Required |
| `survivors[].id` | Unique string matching `[a-z][a-z0-9-]{0,23}` | Required |
| `survivors[].name` | Nonblank string, at most 24 characters | Required |
| `survivors[].attributes` | Object with all four attributes below | Required |
| `attributes.intelligence` | Integer 1–10 | Required |
| `attributes.endurance` | Integer 1–10 | Required |
| `attributes.strength` | Integer 1–10 | Required |
| `attributes.perception` | Integer 1–10 | Required |
| `survivors[].personality` | Object with all three traits below | Required |
| `personality.generosity` | Integer 1–10 | Required |
| `personality.cooperation` | Integer 1–10 | Required |
| `personality.caution` | Integer 1–10 | Required |
| `survivors[].behavior` | Object containing any behavior keys below | Default object below |
| `behavior.priorities` | All eight goal names below, exactly once, ordered | Default order below |
| `behavior.thresholds.hunger` | Integer 1–10 | 6 |
| `behavior.thresholds.thirst` | Integer 1–10 | 5 |
| `behavior.thresholds.energy` | Integer 1–10 | 4 |
| `behavior.preferences.shelter` | `solo`, `shared`, `either` | `either` |
| `behavior.preferences.combat` | `avoid`, `defend` | `avoid` |
| `behavior.preferences.knowledge` | `share`, `trade`, `private` | `share` |
| `behavior.preferences.food` | `berries`, `fish`, `adaptive` | `adaptive` |
| `behavior.trading.enabled` | Boolean | `true` |
| `behavior.trading.reserve` | Integer 1–10 | 2 |

Only attributes cost points. Their total across the whole team must equal
`team_points`; there is no additional per-survivor total cap. Personality is free
because it expresses tradeoffs rather than a strictly increasing advantage.
Partial behavior objects merge recursively with these defaults:

```json5
{
  priorities: ["escape_danger", "secure_water", "find_food", "recover",
    "establish_shelter", "learn_skills", "help_others", "explore"],
  thresholds: { hunger: 6, thirst: 5, energy: 4 },
  preferences: { shelter: "either", combat: "avoid", knowledge: "share", food: "adaptive" },
  trading: { enabled: true, reserve: 2 },
}
```

### What settings do

- Intelligence maps to existing discovery and planning intelligence. Fishing
  discovery takes `700 - 50 × intelligence` personal ticks after lake discovery
  (age and remembered lake are both required); lessons can teach it earlier.
- Endurance scales passive fatigue by `1.5 - endurance/10` and energy recovery
  by `0.5 + endurance/10`.
- Strength scales combat damage by `0.5 + strength/10`, construction energy
  cost by `1.5 - strength/10`, and grants an extra gathered resource at 8–10.
- Perception gives Manhattan visibility radius `1 + floor(perception/2)`.
- Generosity influences pricing, gifts and willingness to accept asymmetric deals;
  cooperation influences joint work; caution influences trust and abandonment.
  Player traits use 1–10; internally existing controllers use the equivalent 10–100.

Priorities rank currently relevant objectives, not unconditional commands.
`secure_water` seeks an unknown water source; `find_food` builds a personal food
stock; `recover` restores low energy; `establish_shelter` seeks a home;
`learn_skills` prepares a rod after fishing is learned or pursues a useful learned
axe/spear project based on gathering demand and danger; `help_others` handles
pending local knowledge requests; `explore` is always relevant. Moving exploration
earlier can intentionally prevent lower-ranked projects. Critical needs,
immediate threats, active joint projects and interrupted sleep retain safeguards;
`escape_danger` is always handled first regardless of its submitted position.
Discovery itself is passive experience, not a separate research action.

Thresholds map to the internal 0–100 needs meter by multiplying by ten.
Hunger/thirst trigger at or above the threshold; energy at or below it. Hard
safety overrides still apply at hunger 68, thirst 64, energy 30, so extreme
thresholds cannot disable basic survival behavior.

`solo` declines joint construction; `shared` seeks a partner subject to cooperation
and local opportunities; `either` accepts the existing opportunistic policy.
`avoid` prefers flight when possible; `defend` permits fighting when not outmatched.
`share` answers known information freely, `trade` quotes a price, `private` declines.
None grants hidden information or guarantees that another survivor agrees.

`berries` favors known forage; `fish` favors fishing even if berries are carried;
`adaptive` uses berries on hand and turns to learned fishing as an alternative.
A berry removes 10 hunger points, fish 50, and a two-berry ration 20 (no free
nutrition gain). Fishing requires a learned skill, four-wood rod, nearby lake,
three energy and an eight-tick cast cooldown. Food preferences do not prohibit
emergency consumption.

Hunting adds raw meat (35 hunger per portion). Learned bow-making and hunting
enable hunting visible deer, boars, or rabbits; bow crafting costs five wood and
one stone. Adaptive food plans weigh nearby prey against known berries, while
fish preference retains practical fishing opportunities. There is no new food
preference enum in this version. Wildlife initially includes five deer, four
boars, and eight rabbits on every preset, with no replacement spawns. Spawn,
movement, and hunting RNG streams cannot alter scheduled predator arrivals.

Trading reserves count total food portions and filled water flasks separately.
They constrain exchanges, not consumption. Disabled trading declines item and
paid-information exchanges; free knowledge sharing and construction remain separate.

## Competition JSON: complete accepted keys and values

Every key except `starting_season` is required. This file is server-owned, not part of a player's entry.

| Key | Accepted values |
| --- | --- |
| `schema_version` | Integer `1` |
| `ruleset_version` | `island-v1` |
| `map_name` | `easy-island`, `balanced-island`, `hard-island` |
| `starting_season` | `spring` (default), `summer`, `autumn`, `winter` |
| `team_points` | Integer from `4 × survivor_count` through `40 × survivor_count` |
| `survivor_count` | Integer 1–20; standard competition uses 5 |
| `max_ticks` | Integer 1–1,000,000 |
| `seeds` | 1–100 distinct integer seeds, each 0–4,294,967,295 |
| `decision_mode` | `rules` |
| `scoring` | `team-survival-v1` |

Map names select versioned presets; seeds select layouts. All currently use a
30×30 island with a central river and three lakes. Terrain generation is shared;
resource counts and predator pressure vary:

| Map | Starting resources | Forest berry regrowth per 80 ticks | Predator wave interval |
| --- | --- | --- | --- |
| `easy-island` | 2× baseline | 4, capped at 7 per tile | 500 ticks |
| `balanced-island` | Baseline | 2, capped at 7 per tile | 250 ticks |
| `hard-island` | 0.5×, rounded down, minimum 1 for existing resources | 1, capped at 7 per tile | 125 ticks |

First wave occurs at the interval. Two wolf waves then one bear wave repeat.
All presets share a seeded temperate climate, starting in spring unless configured
otherwise; seasons last 800 ticks. Lightning uses a 0.4% base chance modified by
weather. See the example guide for temperature, exposure, and shelter behavior.
Newcomer arrivals have been removed
from every mode. Every competition ends at its tick limit or final survivor death.

## Fairness and results

Each team plays separate copies of the same seeds. Terrain generation, initial
survivor positions, predator arrivals, predator behavior, weather, and survivor
choices use independent random streams. Predator spawn locations and pack sizes
do not depend on current survivor locations or decisions. Predator reactions,
resource depletion, and deaths legitimately diverge with team behavior.

```text
seed score = sum(original survivor lifetimes) + 5 × ticks all survivors were alive
competition score = arithmetic mean of seed scores
```

Lifetimes are capped at the match limit. The multiplier stays five even when a
custom competition chooses a different team size; compare entries only within
the same competition settings. Results contain normalized team/settings, engine
and ruleset versions, engine source hash, configuration hash, and per-seed lifetime/score breakdowns.
Engine IDs (`agent-1`, etc.) map to the submission survivors in array order.
The runner caps recent trace events at 30,000; the interactive viewer retains its
normal journal/report workflow. This is a local evaluation runner, not an upload
service or a security sandbox. Hosted competitions still need process, time,
memory, and request limits.

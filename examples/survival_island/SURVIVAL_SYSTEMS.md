# Learning, food, clothing, and shelter supplies

These systems work with the reference controller and model-plan tasks. Models
choose intentions; actor-bound tools validate knowledge, materials, access,
energy, location, and current world state before changing anything. No new team
JSON5 keys are required. Existing priorities, traits, food preferences, and
intelligence apply. Optional home improvements yield to urgent survival needs.

## Shared ecosystem

Lake tiles have finite fish stocks: three per tile on balanced maps, six on easy,
one on hard. Each tile replenishes one fish per 80-tick day up to its initial
capacity. Rod catches and bear catches consume the same stock; empty tiles cannot
produce fish. Rivers remain drinking sources, not fisheries. Wildlife consumes
the same berries as survivors. Bears prefer an adjacent accessible human, then
nearby deer/rabbits or lake fish. A catch feeds a bear by 25 hunger and uses its
six-tick attack cooldown. With no human in hunting range, a hungry bear can pursue
deer/rabbits detected within six tiles. Prey still flee; captures are not guaranteed.

There is no wildlife replacement or breeding. Shelters block all animal entry and
attacks. All environmental spawn schedules remain independent of survivor choices;
food depletion and predator diet choices legitimately diverge across teams.

## One skill progression catalog

`progression.py` owns discovery rules and crafting recipes. Internal intelligence
is the submitted 1–10 value multiplied by ten. Age means that survivor's lifetime,
not time since the beginning of a different run.

| Skill | Minimum discovery age, ticks | Other discovery requirements |
| --- | --- | --- |
| Spear | `180 - intelligence` | Remember wood |
| Stone axe | `350 - 2 × intelligence` | Remember wood and stone |
| Bow | `400 - 2 × intelligence` | Remember wood/stone; have seen wildlife |
| Hunting | No additional wait | Know bow-making and possess a bow |
| Fishing | `700 - 5 × intelligence` | Remember a lake |
| Gardening | `450 - 2 × intelligence` | Remember wood/berries; have a valid home |
| Fire | `350 - 2 × intelligence` | Remember wood and stone |
| Cooking | `450 - 2 × intelligence` | Know fire-making; carry raw fish or meat |
| Skinning | `600 - 3 × intelligence` | Know hunting; have made at least one hunting kill |
| Leatherworking | `800 - 3 × intelligence` | Know skinning; carry a hide |
| Reservoirs | `500 - 2 × intelligence` | Remember wood/stone; have a valid home |

Local lessons can bypass discovery age and observation requirements, but not
prerequisite knowledge. A taught survivor still needs the right equipment and
materials to use a skill. Gifts, paid lessons, and refusals follow existing
communication and trade policies. Paid lessons with missing prerequisites are
rejected before payment; unready free lessons are recorded as deferred.

The catalog implements bbagents `SkillSource`, with reviewed versioned `Skill`
and `SkillDescriptor` guidance. Learning produces procedural `MemoryRecord`s and
trace events containing source, revision, prerequisites, and discovery age. This
is game-owned progression, not model-weight training or arbitrary script execution.
Gameplay inference limits remain unchanged. Model snapshots now have a bounded
14,000-character budget to include the expanded catalog and home state; the Ollama
context remains 8,192 tokens.

## Vegetables

`plant()` costs two berries (organic starter/compost), one wood, one filled flask,
and three energy. Gathering a wild vegetable seed mix is abstracted into planting;
berries are not literally converted into vegetable seeds. The empty flask returns.
Plant on empty grass directly adjacent to a valid home, outside winter. One active
plot is allowed per shelter; plots cannot overlap construction projects.

Plots mature at 160 growth. Each tick adds one in spring/summer, half in autumn,
zero in winter; rain adds another half when growth is possible. Temperatures below
−5°C kill crops. After 160 hot, non-rainy ticks above 32°C a plot fails from drought;
rain resets its drought counter. No extra irrigation tool is required in this version.

`harvest()` at a mature accessible plot costs two energy and yields six vegetables,
then removes the plot. Vegetables restore 30 hunger each and can be traded or
inherited. Shared residents share plots; claimants inherit access through the home.
Survivors invest only with spare supplies and nonurgent needs, then pursue other
goals while crops grow. Ripe plots can supply food plans. Lightning destroying the
associated shelter also destroys its plot. There is no renewable free seed/food action.

## Fire and cooking

`light_fire()` requires learned fire-making. A new fire costs three wood, two stone,
and three energy and burns for 80 ticks. Refueling an existing fire costs one wood
and three energy, adding 40 ticks up to a cap of 120. Outdoors, rain/snow consumes
two fuel ticks per simulation tick; covered fires burn one. Fires expire without fuel.
Fire can be lit on grass/forest or inside one's own/shared home, not someone else's
private shelter. Campfires do not become shelters and do not repel predators.

A burning fire adds 8°C effective warmth on its tile and adjacent tiles; multiple
fires do not stack. Survivors can light/refuel fires when cold or preparing food.
`cook("fish")` or `cook("meat")` consumes one raw portion and two energy near a fire.
Cooking requires its own learned skill. Cooked fish restores 70 hunger (raw: 50);
cooked meat restores 55 (raw: 35). Both can be traded and inherited. Fires have no
spread, smoke, or accidental-burning mechanic yet.

## Skinning, leather, and clothing

`craft("knife")` needs hunting knowledge and costs one wood/two stone: a **stone
knife**, not a metal blade. `skin(animal_id)` requires learned skinning, the knife,
a visible dead animal within one tile, and three energy. Each carcass can be skinned
once globally, yielding two hides for deer, three for boars, or one for rabbits.
Bear kills can leave skinnable carcasses but do not give survivors meat.

At a valid home, `tan_hide()` requires leatherworking and consumes one hide, one
wood (bark/tannin supply), one filled flask, and three energy; it returns an empty
flask and one leather. `craft("leather_garment")` consumes two leather and two energy.
`equip("leather_garment")` enables 6°C effective cold protection while that garment
remains in inventory. Receiving clothing does not automatically equip it; selling
or losing it removes protection. Multiple garments do not stack. Severe cold can
still kill a clothed survivor. Items are inherited; knowledge is not.

## Shelter reservoirs

`craft("reservoir")` needs the learned reservoir skill, six wood, three stone,
and two energy. `install_reservoir()` consumes that item and three energy at a
valid home. One reservoir per shelter holds **ten flask-units**, initially empty.
`fill_reservoir()` transfers one carried `water` into it, returns an empty flask,
and costs one energy. A full reservoir rejects filling without spending supplies.
There is no spontaneous or rain-based water generation.

`drink_reservoir()` consumes one unit and restores 50 thirst. Residents must be
physically at the shelter; there is no remote drinking. Owners and registered
shared residents can use it; visiting sleepers cannot spend its supplies. Claiming
an abandoned shelter grants access to its installed reservoir and remaining water
without needing to learn construction. Lightning destroys both the reservoir and
contents. The reference controller refills a low reservoir to a three-unit working
buffer with spare time; capacity remains ten. Urgent water plans prefer a stocked
home reservoir and fall back to carried water or natural sources when empty.

## Plans, observations, and reports

New model milestones: `grow_vegetables`, `make_fire`, `cook_food`, `make_clothing`,
`store_water`. Planting completes its milestone once a crop is growing; the model
does not waste a new inference or 160 idle actions waiting for harvest. Every turn
revalidates tools against current state. Optional projects respect `learn_skills`
versus `explore` priority and do not bypass emergency responses or model cooldowns.

Green plot outlines turn yellow-green when mature. Orange fire dots show burning
fires, and blue reservoir numbers show stored units on shelter tiles. Hover over
plots, fires, or shelters for progress/fuel/water information. Observations and
model context include relevant home state and visible carcasses. Memory and traces
record planting, harvest, fuel, cooking, skinning, tanning, equipment, water transfers,
and destruction; the Markdown/HTML report includes this evidence and final home state.

The example uses bbagents tools, plans, actor-scoped `ActionRuntime` authorization,
versioned skill guidance, procedural/episodic memory, bounded context building and
existing trace/report machinery. Homestead mutations run serially on the simulation
thread; this is not a new distributed transaction system or a hosted security sandbox.

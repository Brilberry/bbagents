"""Deterministic, headless survival-island simulation.

The world owns state transitions.  Agents may choose only from registered tools;
they never mutate world state directly.  This keeps a future model-backed policy
behind the same boundary as the deterministic reference policy used here.
"""

from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import random
from typing import Any, Iterable

from bbagents import FunctionTool, Observation, Plan, PlanStep, StepStatus, ToolCall, ToolRegistry
from bbagents.memory import MemoryRecord, MemoryStore
from bbagents.persistence import InMemoryStateStore
from .tracing import IslandTrace


Position = tuple[int, int]


class Terrain(str, Enum):
    GRASS = "grass"
    FOREST = "forest"
    LAKE = "lake"
    RIVER = "river"
    MOUNTAIN = "mountain"

    @property
    def is_water(self) -> bool:
        return self in (Terrain.LAKE, Terrain.RIVER)


class Resource(str, Enum):
    WOOD = "wood"
    BERRIES = "berries"
    STONE = "stone"


@dataclass(slots=True)
class Tile:
    terrain: Terrain
    resources: Counter[str] = field(default_factory=Counter)
    shelter_owner: str | None = None


@dataclass(slots=True)
class Message:
    sender: str
    recipient: str
    text: str
    tick: int


@dataclass(slots=True)
class Predator:
    id: str
    kind: str
    position: Position
    health: float
    pack_id: str | None = None
    next_attack_tick: int = 0
    hunger: float = 60.0
    thirst: float = 12.0
    provoked_until: int = -1
    last_needs_tick: int = -1
    behavior: str = "roaming"

    @property
    def alive(self) -> bool:
        return self.health > 0

    def hunting_radius(self, tick: int) -> int:
        if tick <= self.provoked_until:
            return 10
        return 10 if self.hunger >= 85 else 6 if self.hunger >= 60 else 2 if self.hunger >= 40 else 0


@dataclass(slots=True)
class Survivor:
    id: str
    name: str
    position: Position
    objective: str
    health: float = 100.0
    hunger: float = 12.0
    thirst: float = 12.0
    energy: float = 100.0
    visibility: int = 2
    inventory: Counter[str] = field(default_factory=Counter)
    known_tiles: dict[Position, Terrain] = field(default_factory=dict)
    known_resources: dict[str, set[Position]] = field(default_factory=dict)
    inbox: deque[Message] = field(default_factory=deque)
    current_plan: Plan | None = None
    plan_reason: str = ""
    last_action: str = "waiting"
    sheltered: bool = False
    alive: bool = True
    feedback: deque[Observation] = field(default_factory=lambda: deque(maxlen=8))
    last_failure: Observation | None = None
    sleep_remaining: int = 0
    sleep_elapsed: int = 0
    night_cycle: int = -1
    suspended_plan_key: str | None = None
    resume_pending: bool = False
    memory: MemoryStore = field(default_factory=MemoryStore)
    next_attack_tick: int = 0
    home: Position | None = None
    death_tick: int | None = None
    death_cause: str | None = None
    decision_source: str = "rule"
    intelligence: int = 50
    known_survivors: dict[str, Position] = field(default_factory=dict)
    shared_water: set[tuple[str, Position]] = field(default_factory=set)


class Island:
    """Seeded island generator with a central river, three lakes, and resources."""

    def __init__(self, size: int = 30, seed: int = 7) -> None:
        self.size = size
        self.random = random.Random(seed)
        self.tiles = [
            [Tile(Terrain.GRASS) for _ in range(size)] for _ in range(size)
        ]
        self._generate()

    def _generate(self) -> None:
        river_x = self.size // 2
        for y in range(self.size):
            previous_x = river_x
            river_x = max(self.size // 2 - 2, min(self.size // 2 + 2,
                          river_x + self.random.choice((-1, 0, 0, 1))))
            for x in range(min(previous_x, river_x), max(previous_x, river_x) + 1):
                self.tiles[y][x].terrain = Terrain.RIVER
        for _ in range(11):
            cx = self.random.randrange(2, self.size - 2)
            cy = self.random.randrange(2, self.size - 2)
            terrain = self.random.choice((Terrain.FOREST, Terrain.FOREST, Terrain.MOUNTAIN))
            radius = self.random.choice((2, 2, 3))
            for y in range(max(0, cy - radius), min(self.size, cy + radius + 1)):
                for x in range(max(0, cx - radius), min(self.size, cx + radius + 1)):
                    if abs(x - cx) + abs(y - cy) <= radius and not self.tiles[y][x].terrain.is_water:
                        self.tiles[y][x].terrain = terrain

        # Spread water across three regions, with seeded size/position variation.
        # Paint after terrain, leaving a walkable shoreline around each lake.
        self.lake_centers = []
        for fx, fy in ((0.23, 0.27), (0.77, 0.33), (0.23, 0.77)):
            radius = max(1, min(self.size // 10, self.random.randint(2, 3)))
            margin = radius + 1
            cx = max(margin, min(self.size - margin - 1,
                                round((self.size - 1) * fx) + self.random.randint(-1, 1)))
            cy = max(margin, min(self.size - margin - 1,
                                round((self.size - 1) * fy) + self.random.randint(-1, 1)))
            if self.size >= 20:
                # Keep a land bank between each lake and the central river.
                cx = (min(cx, self.size // 2 - radius - 4) if fx < 0.5 else
                      max(cx, self.size // 2 + radius + 4))
            self.lake_centers.append((cx, cy))
            for y in range(max(0, cy - radius - 1), min(self.size, cy + radius + 2)):
                for x in range(max(0, cx - radius - 1), min(self.size, cx + radius + 2)):
                    distance = (x - cx) ** 2 + (y - cy) ** 2
                    if distance <= radius ** 2:
                        self.tiles[y][x].terrain = Terrain.LAKE
                    elif distance <= (radius + 1) ** 2 and self.tiles[y][x].terrain != Terrain.RIVER:
                        self.tiles[y][x].terrain = Terrain.GRASS

        for row in self.tiles:
            for tile in row:
                if tile.terrain == Terrain.FOREST:
                    tile.resources[Resource.WOOD.value] = self.random.randint(8, 18)
                    if self.random.random() < 0.65:
                        tile.resources[Resource.BERRIES.value] = self.random.randint(2, 7)
                elif tile.terrain == Terrain.MOUNTAIN:
                    tile.resources[Resource.STONE.value] = self.random.randint(6, 14)
                elif tile.terrain == Terrain.GRASS and self.random.random() < 0.05:
                    tile.resources[Resource.BERRIES.value] = self.random.randint(1, 4)

    def tile(self, position: Position) -> Tile:
        x, y = position
        return self.tiles[y][x]

    def in_bounds(self, position: Position) -> bool:
        x, y = position
        return 0 <= x < self.size and 0 <= y < self.size

    def passable(self, position: Position) -> bool:
        return self.in_bounds(position) and self.tile(position).terrain != Terrain.MOUNTAIN

    def positions_within(self, center: Position, radius: int) -> Iterable[Position]:
        cx, cy = center
        for y in range(max(0, cy - radius), min(self.size, cy + radius + 1)):
            for x in range(max(0, cx - radius), min(self.size, cx + radius + 1)):
                if abs(x - cx) + abs(y - cy) <= radius:
                    yield x, y


class Simulation:
    DIRECTIONS: dict[str, Position] = {
        "north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)
    }

    def __init__(self, *, size: int = 30, agents: int = 5, seed: int = 7,
                 predator_interval: int | None = 250,
                 survivor_interval: int | None = 1000,
                 lightning_probability: float = 0.004) -> None:
        if predator_interval is not None and predator_interval < 1:
            raise ValueError("predator_interval must be positive or None")
        if survivor_interval is not None and survivor_interval < 1:
            raise ValueError("survivor_interval must be positive or None")
        if not 0 <= lightning_probability <= 1:
            raise ValueError("lightning_probability must be between 0 and 1")
        self.world = Island(size, seed)
        self.predator_interval = predator_interval
        self.predators: list[Predator] = []
        self.predator_waves = 0
        self.survivor_interval = survivor_interval
        self.lightning_probability = lightning_probability
        self.weather_random = random.Random(seed + 91)
        self.last_lightning: tuple[Position, int] | None = None
        self.random = random.Random(seed + 1)
        self.intelligence_random = random.Random(seed + 211)
        self.tick = 0
        self.ended_tick: int | None = None
        self.day_length = 80
        self.messages: deque[Message] = deque(maxlen=100)
        self.event_log: deque[str] = deque(maxlen=120)
        self.trace = IslandTrace(self)
        self._recorded_deaths: set[str] = set()
        self.checkpoints = InMemoryStateStore()
        names = ("Ari", "Bo", "Cyra", "Dax", "Ena")
        self.agents: list[Survivor] = []
        land = [(x, y) for y, row in enumerate(self.world.tiles)
                for x, tile in enumerate(row) if tile.terrain == Terrain.GRASS]
        for index in range(agents):
            separated = [pos for pos in land if all(self.distance(pos, other.position) >= 7
                         for other in self.agents)]
            position = self.random.choice(separated or land)
            self.agents.append(
                Survivor(
                    id=f"agent-{index + 1}",
                    name=names[index % len(names)],
                    position=position,
                    objective="Explore and survive independently",
                    intelligence=self.intelligence_random.randint(
                        85 if index == 0 else 5 if index == 1 else 20,
                        100 if index == 0 else 20 if index == 1 else 85),
                )
            )
        self.policy = SurvivalPolicy(self)
        self.tools = {agent.id: self._make_tools(agent) for agent in self.agents}
        for agent in self.agents:
            self.observe(agent)
            self.policy._update_objective(agent)
            self.policy.replan(agent, "initial objective")
        self._record_states()

    @property
    def is_night(self) -> bool:
        return self.tick % self.day_length >= int(self.day_length * 0.68)

    @property
    def game_over(self) -> bool:
        return self.ended_tick is not None or not any(agent.alive for agent in self.agents)

    def _finish_game(self) -> None:
        if self.ended_tick is None:
            self.ended_tick = self.tick
            self.event_log.appendleft(f"Game over at tick {self.tick}: no survivors remain")
            self.trace.record("simulation.ended", {"tick": self.tick, "reason": "no survivors remain"})

    def _find_spawn(self) -> Position:
        center = self.world.size // 2
        for radius in range(self.world.size):
            for position in self.world.positions_within((center, center), radius):
                if self.world.tile(position).terrain == Terrain.GRASS:
                    return position
        raise RuntimeError("island has no passable spawn")

    def _make_tools(self, agent: Survivor) -> ToolRegistry:
        return ToolRegistry([
            FunctionTool("observe_surroundings", "Observe visible terrain, resources, agents, and structures", lambda: self.observe(agent)),
            FunctionTool("move", "Move one tile: north, south, east, or west", lambda direction: self.move(agent, direction)),
            FunctionTool("gather", "Gather a resource from the current tile", lambda resource: self.gather(agent, resource)),
            FunctionTool("craft", "Craft an item from inventory resources", lambda item: self.craft(agent, item)),
            FunctionTool("build", "Build a structure on the current tile", lambda structure: self.build(agent, structure)),
            FunctionTool("eat", "Eat an inventory item", lambda item: self.eat(agent, item)),
            FunctionTool("drink", "Drink when on or next to a lake or river", lambda: self.drink(agent)),
            FunctionTool("flee", "Sprint two tiles away from a visible predator", lambda: self.flee(agent)),
            FunctionTool("attack", "Attack an adjacent predator", lambda predator_id: self.attack(agent, predator_id)),
            FunctionTool("rest", "Rest and recover health when safe", lambda: self.rest(agent)),
            FunctionTool("sleep", "Start a 12-tick sleep; danger or urgent needs wake you", lambda: self.sleep(agent)),
            FunctionTool("claim_shelter", "Claim the unoccupied shelter at this tile", lambda: self.claim_shelter(agent)),
            FunctionTool("communicate", "Send a message to another agent", lambda agent_id, message: self.communicate(agent, agent_id, message)),
        ])

    def step(self) -> None:
        if self.game_over:
            self._record_states()
            self._finish_game()
            return
        self.tick += 1
        if self.survivor_interval and self.tick % self.survivor_interval == 0:
            self._spawn_survivor()
        if self.weather_random.random() < self.lightning_probability:
            self.lightning_strike()
        if self.predator_interval and self.tick % self.predator_interval == 0:
            self._spawn_predator()
        if self.tick % self.day_length == 0:
            for row in self.world.tiles:
                for tile in row:
                    if tile.terrain == Terrain.FOREST:
                        tile.resources["berries"] = min(7, tile.resources["berries"] + 2)
        for agent in self.agents:
            if not agent.alive:
                continue
            self._apply_needs(agent)
            if not agent.alive:
                if agent.sleep_remaining:
                    self._wake(agent, "death", interrupted=True)
                continue
            if agent.sleep_remaining:
                self._advance_sleep(agent)
                if agent.sleep_remaining:
                    continue
            agent.decision_source = "rule"
            call = self.policy.decide(agent)
            self.trace.record("tool.started", {"tick": self.tick, "agent": agent.id, "tool_name": call.tool_name, "arguments": dict(call.arguments), "source": agent.decision_source})
            try:
                result = self.tools[agent.id].execute(call)
                if hasattr(self.policy, "on_result"):
                    self.policy.on_result(agent, call, result=result)
                agent.feedback.append(result)
                agent.last_action = result.content
                if agent.current_plan is not None and agent.decision_source != "model plan":
                    active_step = next(
                        (
                            step
                            for step in agent.current_plan.steps
                            if step.status == StepStatus.PENDING
                        ),
                        None,
                    )
                    if active_step is not None:
                        active_step.evidence.append(result.content)
                self.trace.record("tool.finished", {"tick": self.tick, "agent": agent.id, "tool_name": call.tool_name, "status": "completed", "result": result.content})
            except (ValueError, KeyError) as exc:
                if hasattr(self.policy, "on_result"):
                    self.policy.on_result(agent, call, error=exc)
                agent.feedback.append(Observation("tool.failure", str(exc), {
                    "tool": call.tool_name, "arguments": dict(call.arguments),
                    "position": agent.position, "tick": self.tick,
                }))
                agent.last_failure = agent.feedback[-1]
                agent.last_action = f"failed: {exc}"
                self.trace.record("tool.finished", {"tick": self.tick, "agent": agent.id, "tool_name": call.tool_name, "status": "failed", "error": str(exc)})
                self.policy.replan(agent, f"tool failure: {exc}")
        self._update_predators()
        self._record_states()
        if self.game_over:
            self._finish_game()

    def _record_states(self) -> None:
        for agent in self.agents:
            if not agent.alive and agent.id in self._recorded_deaths:
                continue
            model_plan = getattr(self.policy, "model_plans", {}).get(agent.id)
            active_plan = model_plan if agent.decision_source == "model plan" else agent.current_plan
            self.trace.record("survivor.state", {"agent": agent.id, "name": agent.name,
                "intelligence": agent.intelligence,
                "objective": agent.objective, "known_survivors": dict(agent.known_survivors),
                "health": agent.health, "hunger": agent.hunger, "thirst": agent.thirst,
                "energy": agent.energy, "position": agent.position, "inventory": dict(agent.inventory),
                "home": agent.home, "sleep_remaining": agent.sleep_remaining,
                "alive": agent.alive, "last_action": agent.last_action,
                "plan_goal": active_plan.goal if active_plan else None,
                "model_plan_goal": model_plan.goal if model_plan else None,
                "plan_reason": agent.plan_reason,
                "visible_predators": [{"id": item.id, "kind": item.kind, "position": item.position,
                                       "health": item.health, "pack_id": item.pack_id,
                                       "hunger": item.hunger, "thirst": item.thirst,
                                       "behavior": item.behavior}
                                      for item in self.visible_predators(agent)]})
            if not agent.alive:
                agent.death_tick = self.tick
                agent.death_cause = agent.death_cause or "unknown"
                self._recorded_deaths.add(agent.id)
                self.trace.record("survivor.died", {"agent": agent.id, "name": agent.name,
                    "cause": agent.death_cause or "unknown", "position": agent.position})

    def _save_sleep_checkpoint(self, agent: Survivor, status: str) -> None:
        if agent.suspended_plan_key is None:
            agent.suspended_plan_key = f"sleep:{agent.id}:{self.tick}"
        key = agent.suspended_plan_key
        previous = self.checkpoints.load(key)
        if previous is None:
            plan = agent.current_plan
            value = {"plan": {"goal": plan.goal, "version": plan.version,
                              "steps": [{"id": step.id, "title": step.title,
                                         "description": step.description,
                                         "status": step.status.value,
                                         "depends_on": list(step.depends_on),
                                         "evidence": list(step.evidence), "error": step.error}
                                        for step in plan.steps]} if plan else None,
                     "reason": agent.plan_reason}
        else:
            value = dict(previous.value)
        value.update(status=status, remaining=agent.sleep_remaining,
                     elapsed=agent.sleep_elapsed, tick=self.tick, position=agent.position)
        saved = self.checkpoints.save(key, value,
            expected_version=previous.version if previous else 0)
        self.trace.record("sleep.checkpoint", {"agent": agent.id, "key": key,
                                               "version": saved.version, "status": status})

    def sleep_site_available(self, agent: Survivor, position: Position) -> bool:
        if self.world.tile(position).shelter_owner is None:
            return True
        return not any(other.id != agent.id and other.alive and other.sleep_remaining
                       and other.position == position for other in self.agents)

    def sleep(self, agent: Survivor) -> Observation:
        if agent.sleep_remaining or self.threatening_predators(agent) or agent.hunger >= 80 or agent.thirst >= 80:
            raise ValueError("sleep requires being awake, safe, fed, and hydrated")
        if not self.sleep_site_available(agent, agent.position):
            raise ValueError("shelter already has a sleeping survivor")
        agent.sleep_remaining = 12
        agent.sleep_elapsed = 0
        agent.resume_pending = False
        agent.night_cycle = self.tick // self.day_length
        self._save_sleep_checkpoint(agent, "sleeping")
        if agent.current_plan and agent.current_plan.goal == "Prepare for night":
            step = agent.current_plan.next_ready_step()
            if step and step.id == "sleep":
                step.status = StepStatus.RUNNING
        self.trace.record("sleep.started", {"agent": agent.id, "tick": self.tick,
                                            "sheltered": self.world.tile(agent.position).shelter_owner is not None})
        self.event_log.appendleft(f"{agent.name} started sleeping")
        return Observation("sleep", "started sleeping (12 ticks)", {"remaining": 12})

    def _advance_sleep(self, agent: Survivor) -> None:
        reason = ("predator" if self.threatening_predators(agent) else
                  "thirst" if agent.thirst >= 80 else
                  "hunger" if agent.hunger >= 80 else None)
        if reason:
            self._wake(agent, reason, interrupted=True)
            return
        sheltered = self.world.tile(agent.position).shelter_owner is not None
        agent.energy = min(100, agent.energy + (5 if sheltered else 2.5))
        agent.health = min(100, agent.health + (2 if sheltered else 1))
        agent.sleep_remaining -= 1
        agent.sleep_elapsed += 1
        agent.last_action = f"sleeping ({agent.sleep_remaining} ticks left)"
        if not agent.sleep_remaining:
            self._wake(agent, "finished", interrupted=False)
        else:
            self._save_sleep_checkpoint(agent, "sleeping")
            self.trace.record("sleep.progress", {"agent": agent.id, "remaining": agent.sleep_remaining,
                                                "health": agent.health, "energy": agent.energy})

    def _wake(self, agent: Survivor, reason: str, *, interrupted: bool) -> None:
        agent.sleep_remaining = 0
        agent.resume_pending = agent.alive
        status = "interrupted" if interrupted else "completed"
        if agent.current_plan and agent.current_plan.goal == "Prepare for night":
            for step in agent.current_plan.steps:
                if step.id == "sleep" and step.status == StepStatus.RUNNING:
                    step.status = StepStatus.FAILED if interrupted else StepStatus.DONE
                    step.error = reason if interrupted else None
        self._save_sleep_checkpoint(agent, status)
        result = Observation("sleep", f"woke: {reason}", {"status": status, "tick": self.tick})
        agent.feedback.append(result)
        agent.last_action = result.content
        agent.memory.remember(MemoryRecord(
            content=f"sleep {status} at {agent.position}: {reason}", source=agent.id,
            metadata={"position": agent.position, "status": status, "reason": reason,
                      "tick": self.tick, "sheltered": self.world.tile(agent.position).shelter_owner is not None}))
        self.trace.record(f"sleep.{status}", {"agent": agent.id, "reason": reason,
                                              "tick": self.tick, "elapsed": agent.sleep_elapsed})
        self.event_log.appendleft(f"{agent.name} woke: {reason}")

    def resume_plan(self, agent: Survivor) -> None:
        saved = self.checkpoints.load(agent.suspended_plan_key) if agent.suspended_plan_key else None
        if saved and saved.value["plan"]:
            data = saved.value["plan"]
            agent.current_plan = Plan(data["goal"], [PlanStep(
                **{**deepcopy(step), "status": StepStatus(step["status"])}) for step in data["steps"]],
                version=data["version"])
            agent.plan_reason = saved.value["reason"]
        self._save_sleep_checkpoint(agent, "resumed")
        self.trace.record("plan.resumed", {"agent": agent.id, "checkpoint": agent.suspended_plan_key,
                                          "tick": self.tick})
        agent.resume_pending = False
        agent.suspended_plan_key = None

    def visible_predators(self, agent: Survivor) -> list[Predator]:
        return [predator for predator in self.predators if predator.alive
                and self.distance(agent.position, predator.position) <= agent.visibility]

    def threatening_predators(self, agent: Survivor) -> list[Predator]:
        return [predator for predator in self.visible_predators(agent)
                if predator.hunting_radius(self.tick) > 0]

    def _spawn_survivor(self) -> None:
        edge = self.world.size - 1
        positions = [(x, y) for x in range(self.world.size) for y in range(self.world.size)
                     if (x in (0, edge) or y in (0, edge)) and self.world.passable((x, y))
                     and not self.world.tile((x, y)).terrain.is_water]
        if not positions:
            return
        number = len(self.agents) + 1
        names = ("Finn", "Gia", "Hale", "Ira", "Juno", "Kai", "Luz")
        name = names[(number - 6) % len(names)]
        if number > 12:
            name += str(number)
        agent = Survivor(f"agent-{number}", name, self.random.choice(positions), "Explore and survive independently",
                         intelligence=self.intelligence_random.randint(5, 100))
        self.agents.append(agent)
        self.tools[agent.id] = self._make_tools(agent)
        self.observe(agent)
        self.policy._update_objective(agent)
        self.policy.replan(agent, "new arrival")
        self.event_log.appendleft(f"Tick {self.tick}: {name} washed ashore")
        self.trace.record("survivor.arrived", {"agent": agent.id, "position": agent.position, "tick": self.tick})

    def shelter_is_available(self, agent: Survivor, position: Position) -> bool:
        owner_id = self.world.tile(position).shelter_owner
        if owner_id is None:
            return False
        owner = next((item for item in self.agents if item.id == owner_id), None)
        return owner_id == agent.id or owner is None or not owner.alive or owner.home != position

    def claim_shelter(self, agent: Survivor) -> Observation:
        if not self.shelter_is_available(agent, agent.position):
            raise ValueError("no unoccupied shelter here")
        self.world.tile(agent.position).shelter_owner = agent.id
        agent.home = agent.position
        self.event_log.appendleft(f"{agent.name} claimed an empty shelter")
        return Observation("claim_shelter", "claimed shelter", {"position": agent.position})

    def lightning_strike(self, position: Position | None = None) -> Observation:
        if position is None:
            position = (self.weather_random.randrange(self.world.size),
                        self.weather_random.randrange(self.world.size))
        if not self.world.in_bounds(position):
            raise ValueError("lightning target outside island")
        destroyed = self.world.tile(position).shelter_owner is not None
        self.world.tile(position).shelter_owner = None
        self.last_lightning = (position, self.tick)
        result = Observation("lightning", "lightning destroyed shelter" if destroyed else "lightning struck ground",
                             {"position": position, "tick": self.tick, "destroyed_shelter": destroyed})
        for agent in self.agents:
            if agent.home == position:
                agent.home = None
            if agent.alive and self.distance(agent.position, position) <= agent.visibility:
                agent.feedback.append(result)
                self.trace.record("calamity.observed", {"agent": agent.id, **result.metadata,
                                                       "content": result.content})
            if agent.alive and agent.position == position:
                agent.sheltered = False
                if agent.sleep_remaining:
                    self._wake(agent, "lightning", interrupted=True)
        self.event_log.appendleft(f"Tick {self.tick}: {result.content} at {position}")
        self.trace.record("calamity.lightning", dict(result.metadata))
        return result

    def _spawn_predator(self) -> None:
        edge = self.world.size - 1
        positions = [(x, y) for x in range(self.world.size) for y in range(self.world.size)
                     if (x in (0, edge) or y in (0, edge))
                     and self.world.passable((x, y))
                     and not self.world.tile((x, y)).terrain.is_water]
        if not positions:
            return
        living = [agent for agent in self.agents if agent.alive]
        safe = [pos for pos in positions if all(self.distance(pos, agent.position) >= 6 for agent in living)]
        self.predator_waves += 1
        kind = "bear" if self.predator_waves % 3 == 0 else "wolf"
        anchor = self.random.choice(safe or positions)
        pack_id = f"pack-{self.predator_waves}" if kind == "wolf" else None
        nearby = [pos for pos in self.world.positions_within(anchor, 2)
                  if self.world.passable(pos) and not self.world.tile(pos).terrain.is_water
                  and all(self.distance(pos, agent.position) >= 4 for agent in living)]
        nearby.sort(key=lambda pos: self.distance(anchor, pos))
        pack_size = self.random.randint(2, 3) if kind == "wolf" else 1
        for index in range(pack_size):
            position = nearby[index % len(nearby)] if nearby else anchor
            predator = Predator(f"predator-{len(self.predators) + 1}", kind,
                                position, 150 if kind == "bear" else 45, pack_id,
                                hunger=self.random.uniform(45, 75), thirst=self.random.uniform(10, 35))
            self.predators.append(predator)
            self.trace.record("predator.spawned", {"tick": self.tick, "id": predator.id,
                                                   "kind": kind, "pack_id": pack_id,
                                                   "hunger": predator.hunger, "thirst": predator.thirst})
        arrival = "a lone bear" if kind == "bear" else f"a pack of {pack_size} wolves"
        self.event_log.appendleft(f"Tick {self.tick}: {arrival} arrived")

    def _merge_wolf_packs(self) -> None:
        wolves = [item for item in self.predators if item.alive and item.kind == "wolf" and item.pack_id]
        for index, wolf in enumerate(wolves):
            for other in wolves[index + 1:]:
                if wolf.pack_id != other.pack_id and self.distance(wolf.position, other.position) <= 1:
                    old = other.pack_id
                    new = wolf.pack_id
                    for member in wolves:
                        if member.pack_id == old:
                            member.pack_id = new
                    size = sum(item.pack_id == new for item in wolves)
                    self.event_log.appendleft(f"Wolf packs merged: {size} wolves")
                    self.trace.record("predator.packs_merged", {"tick": self.tick, "pack_id": new,
                                                               "absorbed": old, "size": size})

    def _apply_predator_needs(self, predator: Predator) -> None:
        if not predator.alive or predator.last_needs_tick == self.tick:
            return
        predator.last_needs_tick = self.tick
        predator.hunger = min(100, predator.hunger + 0.025)
        predator.thirst = min(100, predator.thirst + 0.04)
        damage = (0.08 if predator.hunger >= 100 else 0) + (0.12 if predator.thirst >= 100 else 0)
        predator.health = max(0, predator.health - damage)
        if damage:
            self.trace.record("predator.need_damage", {"predator": predator.id, "damage": damage,
                "hunger": predator.hunger, "thirst": predator.thirst, "health": predator.health})
            if not predator.alive:
                self.trace.record("predator.died", {"predator": predator.id, "cause": "unmet needs"})

    def _water_distances(self) -> dict[Position, int]:
        # One shared breadth-first field per update, not one search per animal.
        distances = {(x, y): 0 for y, row in enumerate(self.world.tiles)
                     for x, tile in enumerate(row) if tile.terrain.is_water}
        queue = deque(distances)
        while queue:
            x, y = queue.popleft()
            for dx, dy in self.DIRECTIONS.values():
                position = (x + dx, y + dy)
                if position not in distances and self.world.passable(position):
                    distances[position] = distances[(x, y)] + 1
                    queue.append(position)
        return distances

    def _feed_predators(self, predator: Predator, prey: Survivor) -> None:
        eaters = [predator] + [other for other in self.predators
            if other is not predator and other.alive and predator.pack_id is not None
            and other.pack_id == predator.pack_id and self.distance(other.position, prey.position) <= 2]
        for eater in eaters:
            eater.hunger = max(0, eater.hunger - (65 if eater is predator else 45))
            eater.behavior = "feeding"
            self.trace.record("predator.fed", {"predator": eater.id, "agent": prey.id,
                                              "hunger": eater.hunger})

    def _update_predators(self) -> None:
        for predator in self.predators:
            self._apply_predator_needs(predator)
        self._merge_wolf_packs()
        # Bears act twice as often as wolves.
        if self.tick % 2:
            return
        # Each pack selects a shared prey using its nearest member's detection.
        # A lone bear chooses independently; unrelated predators never coordinate.
        pack_targets: dict[str, Survivor] = {}
        leaders: dict[str, Predator] = {}
        living = [agent for agent in self.agents if agent.alive]
        water_distances = (self._water_distances()
                           if any(item.alive and item.thirst >= 55 for item in self.predators) else {})
        for predator in self.predators:
            if not predator.alive or predator.pack_id is None or predator.pack_id in leaders:
                continue
            members = [item for item in self.predators if item.alive and item.pack_id == predator.pack_id]
            leaders[predator.pack_id] = predator
            if not living:
                continue
            prey = min(living, key=lambda agent: min(self.distance(item.position, agent.position) for item in members))
            if any(0 < item.hunting_radius(self.tick) >= self.distance(item.position, prey.position)
                   and item.thirst < 85 for item in members):
                pack_targets[predator.pack_id] = prey
        for predator in self.predators:
            if not predator.alive:
                continue
            if predator.kind == "wolf" and self.tick % 4:
                continue
            neighbors = [(predator.position[0] + dx, predator.position[1] + dy)
                         for dx, dy in self.DIRECTIONS.values()]
            neighbors = [pos for pos in neighbors if self.world.passable(pos)
                         and not self.world.tile(pos).terrain.is_water]
            radius = predator.hunting_radius(self.tick)
            living = [agent for agent in self.agents if agent.alive]
            shared = pack_targets.get(predator.pack_id)
            target = (shared if shared is not None and shared.alive else
                      min(living, key=lambda agent: self.distance(predator.position, agent.position), default=None))
            distance = self.distance(predator.position, target.position) if target else float("inf")
            hunt = (radius > 0 and predator.thirst < 85 and target is not None
                    and (shared is not None or distance <= radius))
            if predator.thirst >= 55 and (predator.thirst >= 85 or not hunt or distance > 1):
                if water_distances.get(predator.position) == 1:
                    predator.thirst = max(0, predator.thirst - 65)
                    predator.behavior = "drinking"
                    self.trace.record("predator.drank", {"predator": predator.id,
                        "position": predator.position, "thirst": predator.thirst})
                    continue
                reachable = [pos for pos in neighbors if pos in water_distances]
                if reachable:
                    predator.position = min(reachable, key=water_distances.get)
                    predator.behavior = "seeking water"
                    continue
            predator.behavior = "hunting" if hunt else "roaming"
            if hunt and distance <= 1:
                if self.tick < predator.next_attack_tick:
                    continue
                damage = 30 if predator.kind == "bear" else 7
                predator.next_attack_tick = self.tick + (6 if predator.kind == "bear" else 4)
                if self.world.tile(target.position).shelter_owner:
                    damage *= 0.25
                target.health = max(0, target.health - damage)
                target.alive = target.health > 0
                if not target.alive:
                    target.death_cause = f"{predator.kind} attack ({predator.id})"
                    self._feed_predators(predator, target)
                if target.sleep_remaining:
                    self._wake(target, "predator attack", interrupted=True)
                result = Observation("predator.attack", f"{predator.kind} hit for {damage:g} damage",
                                     {"predator_id": predator.id, "tick": self.tick})
                target.feedback.append(result)
                target.last_action = result.content if target.alive else "killed by predator"
                self.event_log.appendleft(f"Tick {self.tick}: {predator.kind} attacked {target.name}")
                self.trace.record("predator.attacked", {"tick": self.tick, "agent": target.id,
                                                        "predator": predator.id, "damage": damage,
                                                        "pack_id": predator.pack_id,
                                                        "hunger": predator.hunger, "thirst": predator.thirst})
                continue
            if neighbors:
                if hunt:
                    predator.position = min(neighbors, key=lambda pos: self.distance(pos, target.position))
                elif predator.pack_id and leaders[predator.pack_id] is not predator:
                    leader = leaders[predator.pack_id]
                    predator.position = min(neighbors, key=lambda pos: self.distance(pos, leader.position))
                else:
                    predator.position = self.random.choice(neighbors)
        self._merge_wolf_packs()
        for predator in self.predators:
            if predator.alive:
                self.trace.record("predator.state", {"predator": predator.id, "kind": predator.kind,
                    "position": predator.position, "health": predator.health,
                    "hunger": predator.hunger, "thirst": predator.thirst, "behavior": predator.behavior})

    def flee(self, agent: Survivor) -> Observation:
        threats = self.visible_predators(agent)
        if not threats or agent.energy < 3:
            raise ValueError("flee requires a visible predator and 3 energy")
        for _ in range(2):
            neighbors = [(agent.position[0] + dx, agent.position[1] + dy)
                         for dx, dy in self.DIRECTIONS.values()]
            neighbors = [pos for pos in neighbors if self.world.passable(pos)]
            if not neighbors:
                break
            agent.position = max(neighbors, key=lambda pos: min(
                self.distance(pos, predator.position) for predator in threats))
        agent.energy -= 3
        self.observe(agent)
        return Observation("flee", f"fled to {agent.position}", {"position": agent.position})

    def attack(self, agent: Survivor, predator_id: str) -> Observation:
        predator = next((item for item in self.visible_predators(agent)
                         if item.id == predator_id and self.distance(item.position, agent.position) <= 1), None)
        if predator is None or agent.energy < 4 or self.tick < agent.next_attack_tick:
            raise ValueError("attack requires adjacency, 4 energy, and a completed 3-tick cooldown")
        damage = 25 if agent.inventory["spear"] else 12 if agent.inventory["axe"] else 5
        predator.health = max(0, predator.health - damage)
        predator.provoked_until = self.tick + 12
        agent.energy -= 4
        agent.next_attack_tick = self.tick + 3
        reward = {}
        if not predator.alive:
            food, hydration = (50, 20) if predator.kind == "bear" else (30, 10)
            reward = {"hunger_recovered": min(agent.hunger, food),
                      "thirst_recovered": min(agent.thirst, hydration)}
            agent.hunger = max(0, agent.hunger - food)
            agent.thirst = max(0, agent.thirst - hydration)
            self.trace.record("survivor.kill_reward", {"agent": agent.id, "predator": predator.id,
                "kind": predator.kind, **reward, "hunger": agent.hunger, "thirst": agent.thirst})
        outcome = "defeated" if not predator.alive else f"hit for {damage}"
        self.trace.record("predator.defended", {"agent": agent.id, "predator": predator.id,
                                              "damage": damage, "defeated": not predator.alive})
        self.event_log.appendleft(f"{agent.name} {outcome} {predator.kind}")
        recovery = (f"; recovered {reward['hunger_recovered']:g} hunger and "
                    f"{reward['thirst_recovered']:g} thirst" if reward else "")
        return Observation("attack", f"{outcome} {predator.kind}{recovery}",
                           {"predator_id": predator.id, **reward})

    def rest(self, agent: Survivor) -> Observation:
        if self.threatening_predators(agent):
            raise ValueError("cannot rest near a visible predator")
        sheltered = self.world.tile(agent.position).shelter_owner is not None
        recovery = 6 if sheltered else 3
        agent.energy = min(100, agent.energy + recovery)
        if agent.hunger < 68 and agent.thirst < 64:
            agent.health = min(100, agent.health + 0.5)
        return Observation("rest", "rested", {"energy": agent.energy, "health": agent.health,
                                               "sheltered": sheltered, "recovery": recovery})

    def _apply_needs(self, agent: Survivor) -> None:
        agent.sheltered = self.world.tile(agent.position).shelter_owner is not None
        if agent.sleep_remaining:
            return  # Sleep freezes needs and fatigue; predator/lightning damage still applies.
        agent.hunger = min(100.0, agent.hunger + 0.55)
        agent.thirst = min(100.0, agent.thirst + 0.8)
        agent.energy = max(0.0, agent.energy - (0.4 if self.is_night else 0.2))
        damage = 0.0
        causes = []
        if agent.hunger >= 90:
            damage += 1.3
            causes.append("starvation")
        if agent.thirst >= 90:
            damage += 2.1
            causes.append("dehydration")
        if agent.energy <= 2:
            damage += 0.7
            causes.append("exhaustion")
        before = agent.health
        agent.health = max(0.0, agent.health - damage)
        if damage:
            self.trace.record("survivor.damage", {"agent": agent.id, "causes": causes,
                                                "damage": min(before, damage), "health": agent.health})
        if agent.health <= 0:
            agent.alive = False
            agent.death_cause = ", ".join(causes) or "unknown"
            agent.last_action = "died"
            self.event_log.appendleft(f"Tick {self.tick}: {agent.name} died")

    def observe(self, agent: Survivor) -> Observation:
        seen: list[dict[str, Any]] = []
        for position in self.world.positions_within(agent.position, agent.visibility):
            tile = self.world.tile(position)
            agent.known_tiles[position] = tile.terrain
            for resource, positions in agent.known_resources.items():
                if tile.resources[resource] <= 0:
                    positions.discard(position)
            for resource, amount in tile.resources.items():
                if amount > 0:
                    agent.known_resources.setdefault(resource, set()).add(position)
            seen.append({"position": position, "terrain": tile.terrain.value, "resources": dict(tile.resources), "shelter": tile.shelter_owner})
        contacts = self.nearby_survivors(agent)
        for other in contacts:
            self._remember_survivor(agent, other)
        nearby = [other.id for other in contacts]
        threats = [{"id": item.id, "kind": item.kind, "position": item.position, "pack_id": item.pack_id,
                    "health": item.health, "hunger": item.hunger, "thirst": item.thirst,
                    "behavior": item.behavior} for item in self.visible_predators(agent)]
        return Observation("world", f"Observed {len(seen)} tiles and {len(nearby)} nearby agents", {"tiles": seen, "nearby_agents": nearby, "predators": threats})

    def available_actions(self, agent: Survivor) -> list[ToolCall]:
        """Concrete legal calls at this location; message text remains free-form."""
        tile = self.world.tile(agent.position)
        calls = [ToolCall("observe_surroundings")]
        threats = self.threatening_predators(agent)
        if (not threats and not agent.sleep_remaining and agent.hunger < 80 and agent.thirst < 80
                and self.sleep_site_available(agent, agent.position)):
            calls.append(ToolCall("sleep"))
        if threats and agent.energy >= 3:
            calls.append(ToolCall("flee"))
        if not threats:
            calls.append(ToolCall("rest"))
        if self.shelter_is_available(agent, agent.position):
            calls.append(ToolCall("claim_shelter"))
        for predator in threats:
            if agent.energy >= 4 and self.tick >= agent.next_attack_tick and self.distance(agent.position, predator.position) <= 1:
                calls.append(ToolCall("attack", {"predator_id": predator.id}))
        for direction, (dx, dy) in self.DIRECTIONS.items():
            if self.world.passable((agent.position[0] + dx, agent.position[1] + dy)):
                calls.append(ToolCall("move", {"direction": direction}))
        for resource in Resource:
            if tile.resources[resource.value] > 0:
                calls.append(ToolCall("gather", {"resource": resource.value}))
        for item, recipe in {"axe": {"wood": 3, "stone": 1}, "ration": {"berries": 2}, "spear": {"wood": 3}}.items():
            if all(agent.inventory[name] >= count for name, count in recipe.items()):
                calls.append(ToolCall("craft", {"item": item}))
        if tile.terrain == Terrain.GRASS and tile.shelter_owner is None and agent.inventory["wood"] >= 10:
            calls.append(ToolCall("build", {"structure": "shelter"}))
        for item in ("berries", "ration"):
            if agent.inventory[item] > 0:
                calls.append(ToolCall("eat", {"item": item}))
        if any(self.world.tile(pos).terrain.is_water
               for pos in self.world.positions_within(agent.position, 1)):
            calls.append(ToolCall("drink"))
        for other in self.nearby_survivors(agent):
            if other.id in agent.known_survivors:
                calls.append(ToolCall("communicate", {"agent_id": other.id, "message": ""}))
        return calls

    @staticmethod
    def action_is_available(call: ToolCall, available: list[ToolCall]) -> bool:
        for candidate in available:
            if candidate.tool_name != call.tool_name:
                continue
            if call.tool_name == "communicate":
                if candidate.arguments["agent_id"] == call.arguments.get("agent_id"):
                    return True
            elif dict(candidate.arguments) == dict(call.arguments):
                return True
        return False

    def move(self, agent: Survivor, direction: str) -> Observation:
        try:
            dx, dy = self.DIRECTIONS[direction]
        except KeyError as exc:
            raise ValueError(f"invalid direction: {direction}") from exc
        target = (agent.position[0] + dx, agent.position[1] + dy)
        if not self.world.passable(target):
            raise ValueError(f"blocked at {target}")
        agent.position = target
        agent.energy = max(0.0, agent.energy - 1.2)
        self.observe(agent)
        return Observation("move", f"moved {direction} to {target}", {"position": target})

    def gather(self, agent: Survivor, resource: str) -> Observation:
        tile = self.world.tile(agent.position)
        if tile.resources[resource] <= 0:
            agent.known_resources.get(resource, set()).discard(agent.position)
            raise ValueError(f"no {resource} at {agent.position}")
        amount = 2 if resource == Resource.WOOD.value and agent.inventory["axe"] else 1
        amount = min(amount, tile.resources[resource])
        tile.resources[resource] -= amount
        if tile.resources[resource] == 0:
            agent.known_resources.get(resource, set()).discard(agent.position)
        agent.inventory[resource] += amount
        agent.energy = max(0.0, agent.energy - 2.0)
        return Observation("gather", f"gathered {amount} {resource}", {"resource": resource, "amount": amount})

    def craft(self, agent: Survivor, item: str) -> Observation:
        recipes = {"axe": {"wood": 3, "stone": 1}, "ration": {"berries": 2}, "spear": {"wood": 3}}
        if item not in recipes:
            raise ValueError(f"unknown recipe: {item}")
        recipe = recipes[item]
        if any(agent.inventory[name] < count for name, count in recipe.items()):
            raise ValueError(f"missing ingredients for {item}")
        for name, count in recipe.items():
            agent.inventory[name] -= count
        agent.inventory[item] += 1
        agent.energy = max(0.0, agent.energy - 2.0)
        return Observation("craft", f"crafted {item}", {"item": item})

    def build(self, agent: Survivor, structure: str) -> Observation:
        if structure != "shelter":
            raise ValueError(f"unknown structure: {structure}")
        tile = self.world.tile(agent.position)
        if tile.terrain != Terrain.GRASS or tile.shelter_owner is not None:
            raise ValueError("shelters require empty grass")
        if agent.inventory[Resource.WOOD.value] < 10:
            raise ValueError("shelter requires 10 wood")
        agent.inventory[Resource.WOOD.value] -= 10
        tile.shelter_owner = agent.id
        agent.home = agent.position
        agent.energy = max(0.0, agent.energy - 6.0)
        agent.sheltered = True
        self.event_log.appendleft(f"Tick {self.tick}: {agent.name} built a shelter")
        return Observation("build", "built shelter", {"position": agent.position})

    def eat(self, agent: Survivor, item: str) -> Observation:
        nutrition = {"berries": 24.0, "ration": 55.0}
        if item not in nutrition or agent.inventory[item] <= 0:
            raise ValueError(f"no edible {item}")
        agent.inventory[item] -= 1
        agent.hunger = max(0.0, agent.hunger - nutrition[item])
        return Observation("eat", f"ate {item}", {"hunger": agent.hunger})

    def drink(self, agent: Survivor) -> Observation:
        adjacent = self.world.positions_within(agent.position, 1)
        water = next((pos for pos in adjacent if self.world.tile(pos).terrain.is_water), None)
        if water is None:
            raise ValueError("no water within reach")
        agent.thirst = max(0.0, agent.thirst - 75.0)
        return Observation("drink", f"drank from {self.world.tile(water).terrain.value}", {"thirst": agent.thirst})

    def communicate(self, sender: Survivor, agent_id: str, message: str) -> Observation:
        recipient = next((agent for agent in self.nearby_survivors(sender)
                          if agent.id == agent_id and agent.id in sender.known_survivors), None)
        if recipient is None:
            raise ValueError(f"recipient must be discovered and within visibility: {agent_id}")
        self._remember_survivor(recipient, sender)
        water = self.policy.nearest_known_water(sender)
        if water is not None and message == f"{self.world.tile(water).terrain.value}:{water[0]},{water[1]}":
            sender.shared_water.add((recipient.id, water))
        envelope = Message(sender.id, recipient.id, message[:240], self.tick)
        recipient.inbox.append(envelope)
        self.messages.appendleft(envelope)
        self.trace.record("message.sent", {"agent": sender.id, "recipient": recipient.id,
                                           "message": envelope.text})
        return Observation("communicate", f"told {recipient.name}: {message}", {"recipient": recipient.id})

    def nearby_survivors(self, agent: Survivor) -> list[Survivor]:
        return [other for other in self.agents if other.id != agent.id and other.alive
                and self.distance(agent.position, other.position) <= agent.visibility]

    def _remember_survivor(self, agent: Survivor, other: Survivor) -> None:
        if other.id not in agent.known_survivors:
            self.trace.record("survivor.discovered", {"agent": agent.id, "other": other.id,
                                                      "position": other.position})
            agent.memory.remember(MemoryRecord(f"Met {other.name} at {other.position}",
                source="encounter", metadata={"survivor": other.id, "position": other.position,
                                             "tick": self.tick}))
        agent.known_survivors[other.id] = other.position

    @staticmethod
    def distance(a: Position, b: Position) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])


class SurvivalPolicy:
    """Explainable reference policy that demonstrates interruption and replanning."""

    def __init__(self, simulation: Simulation) -> None:
        self.sim = simulation

    def _update_objective(self, agent: Survivor) -> None:
        if agent.suspended_plan_key or agent.resume_pending or self._emergency(agent):
            return
        if self.nearest_known_water(agent) is None:
            objective = "Secure a reliable water source"
        elif agent.home is None:
            objective = "Build a safe shelter"
        elif agent.inventory["berries"] == 0 or (agent.objective.startswith("Stockpile")
                                                  and agent.inventory["berries"] < 6):
            objective = "Stockpile food for myself"
        elif not agent.inventory["spear"]:
            objective = "Prepare personal defenses"
        elif not agent.inventory["axe"]:
            objective = "Develop useful tools"
        else:
            objective = "Explore and map the island"
        if objective != agent.objective:
            previous = agent.objective
            agent.objective = objective
            self.sim.trace.record("objective.changed", {"agent": agent.id, "previous": previous,
                                                        "objective": objective})
            if agent.current_plan is not None:
                self.replan(agent, "priorities changed with local discoveries and needs")

    def _share_useful_water(self, agent: Survivor) -> ToolCall | None:
        water = self.nearest_known_water(agent)
        if water is not None:
            for other in self.sim.nearby_survivors(agent):
                already_shared = any(recipient == other.id and self.sim.distance(position, water) <= 4
                                     for recipient, position in agent.shared_water)
                if other.id in agent.known_survivors and not already_shared:
                    return ToolCall("communicate", {"agent_id": other.id,
                        "message": f"{agent.known_tiles[water].value}:{water[0]},{water[1]}"})
        return None

    def decide(self, agent: Survivor) -> ToolCall:
        self.sim.observe(agent)
        self._read_messages(agent)
        self._update_objective(agent)
        if agent.home is None and agent.current_plan and agent.current_plan.goal == "Maintain existing shelter":
            self.replan(agent, "shelter destroyed")
        defense = self.defend(agent)
        if defense:
            return defense
        emergency = self._emergency(agent)
        if not emergency and not agent.suspended_plan_key and not agent.resume_pending:
            share = self._share_useful_water(agent)
            if share:
                return share
        if (not emergency and not agent.suspended_plan_key and agent.home is None
                and agent.objective.startswith("Build")
                and agent.current_plan and agent.current_plan.goal != "Claim available shelter"
                and self._nearest_known(agent, lambda pos: self.sim.shelter_is_available(agent, pos)) is not None):
            self.replan(agent, "empty shelter discovered")
        if agent.resume_pending and not emergency:
            self.sim.resume_plan(agent)
        if (not emergency and agent.suspended_plan_key is None
                and self.sim.tick % self.sim.day_length >= 44
                and agent.night_cycle != self.sim.tick // self.sim.day_length):
            self.prepare_night(agent)
        if emergency and agent.plan_reason != emergency:
            if agent.suspended_plan_key and agent.current_plan and agent.current_plan.goal == "Prepare for night":
                agent.resume_pending = True
            self.replan(agent, emergency)
        elif agent.current_plan is None:
            self.replan(agent, "resume personal objective")

        assert agent.current_plan is not None
        if agent.current_plan.is_complete():
            if "emergency" in agent.plan_reason:
                self.replan(agent, "resume personal objective")
            else:
                return ToolCall("observe_surroundings")

        for _ in range(len(agent.current_plan.steps) + 1):
            step = agent.current_plan.next_ready_step()
            if step is None:
                self.replan(agent, "plan blocked")
                step = agent.current_plan.next_ready_step()
            assert step is not None
            call, finished = self._call_for_step(agent, step)
            if finished:
                step.status = StepStatus.DONE
                if agent.current_plan.is_complete():
                    return ToolCall("observe_surroundings")
                continue
            self.sim.trace.record("decision.completed", {"tick": self.sim.tick, "agent": agent.id, "reason": agent.plan_reason, "step": step.id, "tool": call.tool_name})
            return call
        return ToolCall("observe_surroundings")

    def prepare_night(self, agent: Survivor) -> None:
        self.sim._save_sleep_checkpoint(agent, "preparing")
        agent.night_cycle = self.sim.tick // self.sim.day_length
        ids = [("prepare-food", "Eat before sleep"), ("prepare-water", "Drink before sleep"),
               ("sleep-site", "Reach a remembered safe shelter if available"),
               ("wait-night", "Wait for night"), ("sleep", "Sleep for 12 ticks")]
        agent.current_plan = Plan("Prepare for night", [PlanStep(
            sid, title, depends_on=[ids[index - 1][0]] if index else [])
            for index, (sid, title) in enumerate(ids)])
        agent.plan_reason = "night preparation"
        self.sim.trace.record("plan.suspended", {"agent": agent.id,
            "checkpoint": agent.suspended_plan_key, "tick": self.sim.tick})

    def defend(self, agent: Survivor) -> ToolCall | None:
        threats = self.sim.threatening_predators(agent)
        if not threats:
            if agent.health < 80 and agent.hunger < 68 and agent.thirst < 64:
                return ToolCall("rest")
            if self.sim.tick >= 200 and not agent.inventory["spear"] and agent.inventory["wood"] >= 3:
                return ToolCall("craft", {"item": "spear"})
            return None
        nearest = min(threats, key=lambda item: self.sim.distance(agent.position, item.position))
        agent.plan_reason = "predator emergency"
        allies = sum(other.alive and other.id != agent.id and self.sim.distance(agent.position, other.position) <= 2
                     for other in self.sim.agents)
        outmatched = (len(threats) > allies + 1 or (nearest.kind == "bear" and allies < 2)
                      or agent.health < 55)
        can_attack = agent.energy >= 4 and self.sim.tick >= agent.next_attack_tick
        if not outmatched and agent.inventory["spear"] and can_attack and self.sim.distance(agent.position, nearest.position) <= 1:
            return ToolCall("attack", {"predator_id": nearest.id})
        if agent.energy >= 3:
            return ToolCall("flee")
        if can_attack and self.sim.distance(agent.position, nearest.position) <= 1:
            return ToolCall("attack", {"predator_id": nearest.id})
        return ToolCall("observe_surroundings")

    def replan(self, agent: Survivor, reason: str) -> None:
        old_version = agent.current_plan.version if agent.current_plan else 0
        if "thirst" in reason:
            steps = [PlanStep("locate-water", "Locate water"), PlanStep("reach-water", "Reach water", depends_on=["locate-water"]), PlanStep("drink", "Drink", depends_on=["reach-water"])]
            goal = "Resolve dangerous thirst"
        elif "hunger" in reason:
            steps = [PlanStep("locate-food", "Locate food"), PlanStep("collect-food", "Collect food", depends_on=["locate-food"]), PlanStep("eat", "Eat", depends_on=["collect-food"])]
            goal = "Resolve dangerous hunger"
        elif "energy" in reason:
            steps = [PlanStep("rest", "Rest")]
            goal = "Recover energy"
        elif (agent.objective.startswith("Build") and agent.home is not None
              and self.sim.world.tile(agent.home).shelter_owner == agent.id):
            steps = [PlanStep("return-home", "Return to existing shelter")]
            goal = "Maintain existing shelter"
        elif (agent.objective.startswith("Build") and self._nearest_known(
                agent, lambda pos: self.sim.shelter_is_available(agent, pos)) is not None):
            steps = [PlanStep("reach-empty-shelter", "Reach an unoccupied shelter"),
                     PlanStep("claim-home", "Claim shelter", depends_on=["reach-empty-shelter"])]
            goal = "Claim available shelter"
        elif agent.objective.startswith("Secure"):
            steps = [PlanStep("locate-water", "Locate water"),
                     PlanStep("reach-water", "Reach water", depends_on=["locate-water"]),
                     PlanStep("drink", "Drink if needed", depends_on=["reach-water"])]
            goal = agent.objective
        elif agent.objective.startswith("Stockpile"):
            steps = [PlanStep("locate-food", "Locate food"), PlanStep("collect-food", "Collect six berries", depends_on=["locate-food"])]
            goal = agent.objective
        elif agent.objective.startswith("Prepare personal"):
            steps = [PlanStep("locate-wood", "Find wood"),
                     PlanStep("collect-wood", "Gather three wood", depends_on=["locate-wood"]),
                     PlanStep("craft-spear", "Craft personal spear", depends_on=["collect-wood"])]
            goal = agent.objective
        elif agent.objective.startswith("Explore"):
            steps = [PlanStep("explore", "Explore unknown territory")]
            goal = agent.objective
        elif agent.objective.startswith("Develop"):
            steps = [PlanStep("locate-wood", "Locate wood"), PlanStep("collect-wood", "Collect wood", depends_on=["locate-wood"]), PlanStep("locate-stone", "Locate stone", depends_on=["collect-wood"]), PlanStep("collect-stone", "Collect stone", depends_on=["locate-stone"]), PlanStep("craft-axe", "Craft axe", depends_on=["collect-stone"])]
            goal = agent.objective
        else:
            steps = [PlanStep("locate-wood", "Locate forest"), PlanStep("collect-wood", "Gather ten wood", depends_on=["locate-wood"]), PlanStep("find-site", "Find a grass building site", depends_on=["collect-wood"]), PlanStep("build-shelter", "Build shelter", depends_on=["find-site"])]
            goal = "Survive the night by building shelter"
        agent.current_plan = Plan(goal, steps, version=old_version + 1)
        agent.plan_reason = reason
        self.sim.trace.record("plan.revised", {"tick": self.sim.tick, "agent": agent.id, "goal": goal, "reason": reason, "version": old_version + 1})
        self.sim.event_log.appendleft(f"Tick {self.sim.tick}: {agent.name} replans — {reason}")

    def _emergency(self, agent: Survivor) -> str | None:
        if agent.thirst >= 64:
            return "thirst emergency"
        if agent.hunger >= 68:
            return "hunger emergency"
        if agent.energy <= 30:
            return "energy emergency"
        return None

    def _call_for_step(self, agent: Survivor, step: PlanStep) -> tuple[ToolCall, bool]:
        sid = step.id
        if sid in {"return-home", "reach-empty-shelter"}:
            target = (agent.home if sid == "return-home" else self._nearest_known(
                agent, lambda pos: self.sim.shelter_is_available(agent, pos)))
            if target is None:
                return self._explore(agent), False
            if agent.position == target:
                return ToolCall("observe_surroundings"), True
            return ToolCall("move", {"direction": self._direction_toward(agent.position, target)}), False
        if sid == "claim-home":
            return ToolCall("claim_shelter"), agent.home == agent.position
        if sid == "prepare-food":
            if agent.hunger < 35:
                return ToolCall("observe_surroundings"), True
            if agent.inventory["berries"]:
                return ToolCall("eat", {"item": "berries"}), False
            return self._collect(agent, "berries", 1)
        if sid == "prepare-water":
            if agent.thirst < 35:
                return ToolCall("observe_surroundings"), True
            water = self.nearest_known_water(agent)
            if water is None:
                return self._explore(agent), False
            if self.sim.distance(agent.position, water) <= 1:
                return ToolCall("drink"), False
            return ToolCall("move", {"direction": self._direction_toward(agent.position, water)}), False
        if sid == "sleep-site":
            unsafe = {tuple(result.record.metadata["position"])
                      for result in agent.memory.retrieve("sleep", limit=8)
                      if result.record.metadata.get("reason", "").startswith("predator")}
            site = self._nearest_known(agent, lambda pos:
                self.sim.world.tile(pos).shelter_owner is not None and pos not in unsafe
                and self.sim.sleep_site_available(agent, pos))
            if site is None or site == agent.position:
                return ToolCall("observe_surroundings"), True
            return ToolCall("move", {"direction": self._direction_toward(agent.position, site)}), False
        if sid == "wait-night":
            return ToolCall("observe_surroundings"), self.sim.is_night
        if sid == "sleep":
            if not self.sim.sleep_site_available(agent, agent.position):
                # Another survivor may have taken the shelter while we waited for night.
                for direction, (dx, dy) in self.sim.DIRECTIONS.items():
                    position = (agent.position[0] + dx, agent.position[1] + dy)
                    if self.sim.world.passable(position) and self.sim.sleep_site_available(agent, position):
                        return ToolCall("move", {"direction": direction}), False
                return ToolCall("observe_surroundings"), False
            return ToolCall("sleep"), False
        if sid.startswith("locate-"):
            target = sid.removeprefix("locate-")
            known = self._target(agent, target)
            return (ToolCall("observe_surroundings"), True) if known is not None else (self._explore(agent), False)
        if sid in {"reach-water", "reach-shelter"}:
            target = self.nearest_known_water(agent) if sid == "reach-water" else self._known_shelter(agent)
            if target is None:
                return self._explore(agent), False
            if sid == "reach-water" and self.sim.distance(agent.position, target) <= 1:
                return ToolCall("observe_surroundings"), True
            if agent.position == target:
                return ToolCall("observe_surroundings"), True
            return ToolCall("move", {"direction": self._direction_toward(agent.position, target)}), False
        if sid == "drink":
            return ToolCall("drink"), agent.thirst < 35
        if sid == "collect-food":
            return self._collect(agent, "berries", 6 if agent.objective.startswith("Stockpile") else 1)
        if sid == "eat":
            if agent.hunger < 35:
                return ToolCall("observe_surroundings"), True
            if agent.inventory["berries"] == 0:
                return self._collect(agent, "berries", 1)
            return ToolCall("eat", {"item": "berries"}), False
        if sid == "collect-wood":
            required = 3 if agent.objective.startswith(("Develop", "Prepare personal")) else 10
            return self._collect(agent, "wood", required)
        if sid == "collect-stone":
            return self._collect(agent, "stone", 1)
        if sid == "craft-axe":
            return ToolCall("craft", {"item": "axe"}), agent.inventory["axe"] > 0
        if sid == "craft-spear":
            return ToolCall("craft", {"item": "spear"}), agent.inventory["spear"] > 0
        if sid == "find-site":
            if self.sim.world.tile(agent.position).terrain == Terrain.GRASS and self.sim.world.tile(agent.position).shelter_owner is None:
                return ToolCall("observe_surroundings"), True
            target = self._nearest_known(agent, lambda pos: self.sim.world.tile(pos).terrain == Terrain.GRASS and self.sim.world.tile(pos).shelter_owner is None)
            return (ToolCall("move", {"direction": self._direction_toward(agent.position, target)}), False) if target else (self._explore(agent), False)
        if sid == "build-shelter":
            return ToolCall("build", {"structure": "shelter"}), self.sim.world.tile(agent.position).shelter_owner == agent.id
        if sid == "share-water":
            water = self.nearest_known_water(agent)
            recipient = next((other for other in self.sim.nearby_survivors(agent)
                              if other.id in agent.known_survivors), None)
            if water is None or recipient is None:
                return ToolCall("observe_surroundings"), True
            if step.evidence:
                return ToolCall("observe_surroundings"), True
            return ToolCall("communicate", {"agent_id": recipient.id, "message": f"{self.sim.world.tile(water).terrain.value}:{water[0]},{water[1]}"}), False
        if sid == "rest":
            return ToolCall("rest"), agent.energy >= 70
        if sid == "explore":
            if len(agent.known_tiles) >= int(self.sim.world.size * self.sim.world.size * 0.65):
                return ToolCall("observe_surroundings"), True
            return self._explore(agent), False
        return ToolCall("observe_surroundings"), False

    def _collect(self, agent: Survivor, resource: str, required: int) -> tuple[ToolCall, bool]:
        if agent.inventory[resource] >= required:
            return ToolCall("observe_surroundings"), True
        positions = agent.known_resources.get(resource, set())
        positions = {pos for pos in positions if self.sim.world.tile(pos).resources[resource] > 0}
        agent.known_resources[resource] = positions
        if not positions:
            return self._explore(agent), False
        target = min(positions, key=lambda pos: self.sim.distance(agent.position, pos))
        if agent.position == target:
            return ToolCall("gather", {"resource": resource}), False
        return ToolCall("move", {"direction": self._direction_toward(agent.position, target)}), False

    def _target(self, agent: Survivor, target: str) -> Position | None:
        if target == "water":
            return self.nearest_known_water(agent)
        if target == "shelter":
            return self._known_shelter(agent)
        return self._nearest_resource(agent, {"food": "berries"}.get(target, target))

    def _nearest_resource(self, agent: Survivor, resource: str) -> Position | None:
        positions = [pos for pos in agent.known_resources.get(resource, set()) if self.sim.world.tile(pos).resources[resource] > 0]
        return min(positions, key=lambda pos: self.sim.distance(agent.position, pos), default=None)

    def nearest_known_terrain(self, agent: Survivor, terrain: Terrain) -> Position | None:
        positions = [pos for pos, known in agent.known_tiles.items() if known == terrain]
        return min(positions, key=lambda pos: self.sim.distance(agent.position, pos), default=None)

    def nearest_known_water(self, agent: Survivor) -> Position | None:
        return self._nearest_known(agent, lambda pos: agent.known_tiles[pos].is_water)

    def _known_shelter(self, agent: Survivor) -> Position | None:
        return self._nearest_known(agent, lambda pos: self.sim.world.tile(pos).shelter_owner is not None)

    def _nearest_known(self, agent: Survivor, predicate: Any) -> Position | None:
        positions = [pos for pos in agent.known_tiles if predicate(pos)]
        return min(positions, key=lambda pos: self.sim.distance(agent.position, pos), default=None)

    def _explore(self, agent: Survivor) -> ToolCall:
        candidates: list[tuple[int, str]] = []
        for direction, (dx, dy) in self.sim.DIRECTIONS.items():
            target = (agent.position[0] + dx, agent.position[1] + dy)
            if self.sim.world.passable(target):
                novelty = sum(1 for pos in self.sim.world.positions_within(target, agent.visibility) if pos not in agent.known_tiles)
                candidates.append((novelty, direction))
        if not candidates:
            return ToolCall("observe_surroundings")
        best = max(score for score, _ in candidates)
        directions = [direction for score, direction in candidates if score == best]
        return ToolCall("move", {"direction": self.sim.random.choice(directions)})

    def _direction_toward(self, start: Position, target: Position) -> str:
        # Breadth-first search avoids greedy oscillation around mountains.
        queue = deque([(start, None)])
        visited = {start}
        while queue:
            position, first = queue.popleft()
            if position == target and first is not None:
                return first
            for direction, (dx, dy) in self.sim.DIRECTIONS.items():
                candidate = (position[0] + dx, position[1] + dy)
                if candidate not in visited and self.sim.world.passable(candidate):
                    visited.add(candidate)
                    queue.append((candidate, first or direction))
        ranked: list[tuple[int, str]] = []
        for direction, (dx, dy) in self.sim.DIRECTIONS.items():
            candidate = (start[0] + dx, start[1] + dy)
            if self.sim.world.passable(candidate):
                ranked.append((self.sim.distance(candidate, target), direction))
        if not ranked:
            return "north"
        best = min(score for score, _ in ranked)
        return self.sim.random.choice([direction for score, direction in ranked if score == best])

    def _read_messages(self, agent: Survivor) -> None:
        while agent.inbox:
            message = agent.inbox.popleft()
            if message.text.startswith(("lake:", "river:")):
                coordinates = message.text.split(":", 1)[1].split(",")
                try:
                    position = int(coordinates[0]), int(coordinates[1])
                except (ValueError, IndexError):
                    continue
                if self.sim.world.in_bounds(position):
                    agent.known_tiles[position] = Terrain(message.text.split(":", 1)[0])
                    self.sim.trace.record("memory.updated", {"tick": self.sim.tick, "agent": agent.id, "fact": "water", "position": position, "source": message.sender})

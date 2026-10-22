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
from bbagents.observability import InMemoryTrace
from bbagents.memory import MemoryRecord, MemoryStore
from bbagents.persistence import InMemoryStateStore


Position = tuple[int, int]


class Terrain(str, Enum):
    GRASS = "grass"
    FOREST = "forest"
    RIVER = "river"
    MOUNTAIN = "mountain"


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

    @property
    def alive(self) -> bool:
        return self.health > 0


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


class Island:
    """Seeded island generator with a guaranteed river and resource regions."""

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
            river_x = max(2, min(self.size - 3, river_x + self.random.choice((-1, 0, 0, 1))))
            self.tiles[y][river_x].terrain = Terrain.RIVER

        for _ in range(11):
            cx = self.random.randrange(2, self.size - 2)
            cy = self.random.randrange(2, self.size - 2)
            terrain = self.random.choice((Terrain.FOREST, Terrain.FOREST, Terrain.MOUNTAIN))
            radius = self.random.choice((2, 2, 3))
            for y in range(max(0, cy - radius), min(self.size, cy + radius + 1)):
                for x in range(max(0, cx - radius), min(self.size, cx + radius + 1)):
                    if abs(x - cx) + abs(y - cy) <= radius and self.tiles[y][x].terrain != Terrain.RIVER:
                        self.tiles[y][x].terrain = terrain

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
                 predator_interval: int | None = 250) -> None:
        if predator_interval is not None and predator_interval < 1:
            raise ValueError("predator_interval must be positive or None")
        self.world = Island(size, seed)
        self.predator_interval = predator_interval
        self.predators: list[Predator] = []
        self.random = random.Random(seed + 1)
        self.tick = 0
        self.day_length = 80
        self.messages: deque[Message] = deque(maxlen=100)
        self.event_log: deque[str] = deque(maxlen=120)
        self.trace = InMemoryTrace()
        self.checkpoints = InMemoryStateStore()
        objectives = (
            "Build a safe shelter",
            "Secure a reliable water source",
            "Stockpile food for the group",
            "Explore and map the island",
            "Develop tools and help the group",
        )
        names = ("Ari", "Bo", "Cyra", "Dax", "Ena")
        spawn = self._find_spawn()
        self.agents: list[Survivor] = []
        for index in range(agents):
            offset = (index % 3 - 1, index // 3)
            position = (spawn[0] + offset[0], spawn[1] + offset[1])
            if not self.world.passable(position):
                position = spawn
            self.agents.append(
                Survivor(
                    id=f"agent-{index + 1}",
                    name=names[index % len(names)],
                    position=position,
                    objective=objectives[index % len(objectives)],
                )
            )
        self.policy = SurvivalPolicy(self)
        self.tools = {agent.id: self._make_tools(agent) for agent in self.agents}
        for agent in self.agents:
            self.observe(agent)
            self.policy.replan(agent, "initial objective")

    @property
    def is_night(self) -> bool:
        return self.tick % self.day_length >= int(self.day_length * 0.68)

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
            FunctionTool("drink", "Drink when on or next to a river", lambda: self.drink(agent)),
            FunctionTool("flee", "Sprint two tiles away from a visible predator", lambda: self.flee(agent)),
            FunctionTool("attack", "Attack an adjacent predator", lambda predator_id: self.attack(agent, predator_id)),
            FunctionTool("rest", "Rest and recover health when safe", lambda: self.rest(agent)),
            FunctionTool("sleep", "Start a 12-tick sleep; danger or urgent needs wake you", lambda: self.sleep(agent)),
            FunctionTool("communicate", "Send a message to another agent", lambda agent_id, message: self.communicate(agent, agent_id, message)),
        ])

    def step(self) -> None:
        self.tick += 1
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
            call = self.policy.decide(agent)
            self.trace.record("tool.started", {"tick": self.tick, "agent": agent.id, "tool_name": call.tool_name, "arguments": dict(call.arguments)})
            try:
                result = self.tools[agent.id].execute(call)
                agent.feedback.append(result)
                agent.last_action = result.content
                if agent.current_plan is not None:
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
                agent.feedback.append(Observation("tool.failure", str(exc), {
                    "tool": call.tool_name, "arguments": dict(call.arguments),
                    "position": agent.position, "tick": self.tick,
                }))
                agent.last_failure = agent.feedback[-1]
                agent.last_action = f"failed: {exc}"
                self.trace.record("tool.finished", {"tick": self.tick, "agent": agent.id, "tool_name": call.tool_name, "status": "failed", "error": str(exc)})
                self.policy.replan(agent, f"tool failure: {exc}")
        self._update_predators()

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

    def sleep(self, agent: Survivor) -> Observation:
        if agent.sleep_remaining or self.visible_predators(agent) or agent.hunger >= 80 or agent.thirst >= 80:
            raise ValueError("sleep requires being awake, safe, fed, and hydrated")
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
        reason = ("predator" if self.visible_predators(agent) else
                  "thirst" if agent.thirst >= 80 else
                  "hunger" if agent.hunger >= 80 else None)
        if reason:
            self._wake(agent, reason, interrupted=True)
            return
        sheltered = self.world.tile(agent.position).shelter_owner is not None
        agent.energy = min(100, agent.energy + (5 if sheltered else 2.5))
        agent.sleep_remaining -= 1
        agent.sleep_elapsed += 1
        agent.last_action = f"sleeping ({agent.sleep_remaining} ticks left)"
        if not agent.sleep_remaining:
            self._wake(agent, "finished", interrupted=False)
        else:
            self._save_sleep_checkpoint(agent, "sleeping")
            self.trace.record("sleep.progress", {"agent": agent.id, "remaining": agent.sleep_remaining})

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

    def _spawn_predator(self) -> None:
        edge = self.world.size - 1
        positions = [(x, y) for x in range(self.world.size) for y in range(self.world.size)
                     if (x in (0, edge) or y in (0, edge))
                     and self.world.passable((x, y))
                     and self.world.tile((x, y)).terrain != Terrain.RIVER]
        if not positions:
            return
        living = [agent for agent in self.agents if agent.alive]
        safe = [pos for pos in positions if all(self.distance(pos, agent.position) >= 6 for agent in living)]
        kind = "bear" if (len(self.predators) + 1) % 3 == 0 else "wolf"
        predator = Predator(f"predator-{len(self.predators) + 1}", kind,
                            self.random.choice(safe or positions), 75 if kind == "bear" else 45)
        self.predators.append(predator)
        self.event_log.appendleft(f"Tick {self.tick}: a {kind} arrived")
        self.trace.record("predator.spawned", {"tick": self.tick, "id": predator.id, "kind": kind})

    def _update_predators(self) -> None:
        # Predators act every four ticks; survivors have time to evade or fight.
        if self.tick % 4:
            return
        for predator in self.predators:
            if not predator.alive:
                continue
            living = [agent for agent in self.agents if agent.alive]
            if not living:
                return
            target = min(living, key=lambda agent: self.distance(predator.position, agent.position))
            distance = self.distance(predator.position, target.position)
            if distance <= 1:
                damage = 12 if predator.kind == "bear" else 8
                if self.world.tile(target.position).shelter_owner:
                    damage *= 0.25
                target.health = max(0, target.health - damage)
                target.alive = target.health > 0
                if target.sleep_remaining:
                    self._wake(target, "predator attack", interrupted=True)
                result = Observation("predator.attack", f"{predator.kind} hit for {damage:g} damage",
                                     {"predator_id": predator.id, "tick": self.tick})
                target.feedback.append(result)
                target.last_action = result.content if target.alive else "killed by predator"
                self.event_log.appendleft(f"Tick {self.tick}: {predator.kind} attacked {target.name}")
                self.trace.record("predator.attacked", {"tick": self.tick, "agent": target.id,
                                                        "predator": predator.id, "damage": damage})
                continue
            neighbors = [(predator.position[0] + dx, predator.position[1] + dy)
                         for dx, dy in self.DIRECTIONS.values()]
            neighbors = [pos for pos in neighbors if self.world.passable(pos)
                         and self.world.tile(pos).terrain != Terrain.RIVER]
            if neighbors:
                predator.position = (min(neighbors, key=lambda pos: self.distance(pos, target.position))
                                     if distance <= 6 else self.random.choice(neighbors))

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
        if predator is None or agent.energy < 2:
            raise ValueError("attack requires an adjacent live predator and 2 energy")
        damage = 25 if agent.inventory["spear"] else 12 if agent.inventory["axe"] else 5
        predator.health = max(0, predator.health - damage)
        agent.energy -= 2
        outcome = "defeated" if not predator.alive else f"hit for {damage}"
        self.trace.record("predator.defended", {"agent": agent.id, "predator": predator.id,
                                              "damage": damage, "defeated": not predator.alive})
        self.event_log.appendleft(f"{agent.name} {outcome} {predator.kind}")
        return Observation("attack", f"{outcome} {predator.kind}", {"predator_id": predator.id})

    def rest(self, agent: Survivor) -> Observation:
        if self.visible_predators(agent):
            raise ValueError("cannot rest near a visible predator")
        sheltered = self.world.tile(agent.position).shelter_owner is not None
        recovery = 6 if sheltered else 3
        agent.energy = min(100, agent.energy + recovery)
        if agent.hunger < 68 and agent.thirst < 64:
            agent.health = min(100, agent.health + 0.5)
        return Observation("rest", "rested", {"energy": agent.energy, "health": agent.health,
                                               "sheltered": sheltered, "recovery": recovery})

    def _apply_needs(self, agent: Survivor) -> None:
        agent.hunger = min(100.0, agent.hunger + 0.55)
        agent.thirst = min(100.0, agent.thirst + 0.8)
        agent.energy = max(0.0, agent.energy - (0.4 if self.is_night else 0.2))
        agent.sheltered = self.world.tile(agent.position).shelter_owner is not None
        damage = 0.0
        if agent.hunger >= 90:
            damage += 1.3
        if agent.thirst >= 90:
            damage += 2.1
        if agent.energy <= 2:
            damage += 0.7
        agent.health = max(0.0, agent.health - damage)
        if agent.health <= 0:
            agent.alive = False
            agent.last_action = "died"
            self.event_log.appendleft(f"Tick {self.tick}: {agent.name} died")

    def observe(self, agent: Survivor) -> Observation:
        seen: list[dict[str, Any]] = []
        discovered_river = False
        for position in self.world.positions_within(agent.position, agent.visibility):
            tile = self.world.tile(position)
            is_new_river = tile.terrain == Terrain.RIVER and position not in agent.known_tiles
            discovered_river = discovered_river or is_new_river
            agent.known_tiles[position] = tile.terrain
            for resource, positions in agent.known_resources.items():
                if tile.resources[resource] <= 0:
                    positions.discard(position)
            for resource, amount in tile.resources.items():
                if amount > 0:
                    agent.known_resources.setdefault(resource, set()).add(position)
            seen.append({"position": position, "terrain": tile.terrain.value, "resources": dict(tile.resources), "shelter": tile.shelter_owner})
        nearby = [other.id for other in self.agents if other.id != agent.id and other.alive and self.distance(agent.position, other.position) <= agent.visibility]
        if discovered_river:
            river = self.policy.nearest_known_terrain(agent, Terrain.RIVER)
            if river is not None:
                for other in self.agents:
                    if other.id != agent.id and other.alive:
                        self.communicate(agent, other.id, f"river:{river[0]},{river[1]}")
        threats = [{"id": item.id, "kind": item.kind, "position": item.position,
                    "health": item.health} for item in self.visible_predators(agent)]
        return Observation("world", f"Observed {len(seen)} tiles and {len(nearby)} nearby agents", {"tiles": seen, "nearby_agents": nearby, "predators": threats})

    def available_actions(self, agent: Survivor) -> list[ToolCall]:
        """Concrete legal calls at this location; message text remains free-form."""
        tile = self.world.tile(agent.position)
        calls = [ToolCall("observe_surroundings")]
        threats = self.visible_predators(agent)
        if not threats and not agent.sleep_remaining and agent.hunger < 80 and agent.thirst < 80:
            calls.append(ToolCall("sleep"))
        if threats and agent.energy >= 3:
            calls.append(ToolCall("flee"))
        if not threats:
            calls.append(ToolCall("rest"))
        for predator in threats:
            if agent.energy >= 2 and self.distance(agent.position, predator.position) <= 1:
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
        if any(self.world.tile(pos).terrain == Terrain.RIVER
               for pos in self.world.positions_within(agent.position, 1)):
            calls.append(ToolCall("drink"))
        for other in self.agents:
            if other.alive and other.id != agent.id:
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
        if not any(self.world.tile(position).terrain == Terrain.RIVER for position in adjacent):
            raise ValueError("no river within reach")
        agent.thirst = max(0.0, agent.thirst - 75.0)
        return Observation("drink", "drank from river", {"thirst": agent.thirst})

    def communicate(self, sender: Survivor, agent_id: str, message: str) -> Observation:
        recipient = next((agent for agent in self.agents if agent.id == agent_id and agent.alive), None)
        if recipient is None:
            raise ValueError(f"unknown recipient: {agent_id}")
        envelope = Message(sender.id, recipient.id, message[:240], self.tick)
        recipient.inbox.append(envelope)
        self.messages.appendleft(envelope)
        return Observation("communicate", f"told {recipient.name}: {message}", {"recipient": recipient.id})

    @staticmethod
    def distance(a: Position, b: Position) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])


class SurvivalPolicy:
    """Explainable reference policy that demonstrates interruption and replanning."""

    def __init__(self, simulation: Simulation) -> None:
        self.sim = simulation

    def decide(self, agent: Survivor) -> ToolCall:
        self._read_messages(agent)
        defense = self.defend(agent)
        if defense:
            return defense
        emergency = self._emergency(agent)
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
        threats = self.sim.visible_predators(agent)
        if not threats:
            if agent.health < 80 and agent.hunger < 68 and agent.thirst < 64:
                return ToolCall("rest")
            if self.sim.tick >= 200 and not agent.inventory["spear"] and agent.inventory["wood"] >= 3:
                return ToolCall("craft", {"item": "spear"})
            return None
        nearest = min(threats, key=lambda item: self.sim.distance(agent.position, item.position))
        agent.plan_reason = "predator emergency"
        if agent.inventory["spear"] and agent.energy >= 2 and self.sim.distance(agent.position, nearest.position) <= 1:
            return ToolCall("attack", {"predator_id": nearest.id})
        if agent.energy >= 3:
            return ToolCall("flee")
        if agent.energy >= 2 and self.sim.distance(agent.position, nearest.position) <= 1:
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
        elif agent.objective.startswith("Secure"):
            steps = [PlanStep("locate-water", "Locate water"), PlanStep("reach-water", "Reach water", depends_on=["locate-water"]), PlanStep("share-water", "Share water location", depends_on=["reach-water"])]
            goal = agent.objective
        elif agent.objective.startswith("Stockpile"):
            steps = [PlanStep("locate-food", "Locate food"), PlanStep("collect-food", "Collect six berries", depends_on=["locate-food"])]
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
        if sid == "prepare-food":
            if agent.hunger < 35:
                return ToolCall("observe_surroundings"), True
            if agent.inventory["berries"]:
                return ToolCall("eat", {"item": "berries"}), False
            return self._collect(agent, "berries", 1)
        if sid == "prepare-water":
            if agent.thirst < 35:
                return ToolCall("observe_surroundings"), True
            water = self.nearest_known_terrain(agent, Terrain.RIVER)
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
                self.sim.world.tile(pos).shelter_owner is not None and pos not in unsafe)
            if site is None or site == agent.position:
                return ToolCall("observe_surroundings"), True
            return ToolCall("move", {"direction": self._direction_toward(agent.position, site)}), False
        if sid == "wait-night":
            return ToolCall("observe_surroundings"), self.sim.is_night
        if sid == "sleep":
            return ToolCall("sleep"), False
        if sid.startswith("locate-"):
            target = sid.removeprefix("locate-")
            known = self._target(agent, target)
            return (ToolCall("observe_surroundings"), True) if known is not None else (self._explore(agent), False)
        if sid in {"reach-water", "reach-shelter"}:
            target = self.nearest_known_terrain(agent, Terrain.RIVER) if sid == "reach-water" else self._known_shelter(agent)
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
            required = 10 if not agent.objective.startswith("Develop") else 3
            return self._collect(agent, "wood", required)
        if sid == "collect-stone":
            return self._collect(agent, "stone", 1)
        if sid == "craft-axe":
            return ToolCall("craft", {"item": "axe"}), agent.inventory["axe"] > 0
        if sid == "find-site":
            if self.sim.world.tile(agent.position).terrain == Terrain.GRASS and self.sim.world.tile(agent.position).shelter_owner is None:
                return ToolCall("observe_surroundings"), True
            target = self._nearest_known(agent, lambda pos: self.sim.world.tile(pos).terrain == Terrain.GRASS and self.sim.world.tile(pos).shelter_owner is None)
            return (ToolCall("move", {"direction": self._direction_toward(agent.position, target)}), False) if target else (self._explore(agent), False)
        if sid == "build-shelter":
            return ToolCall("build", {"structure": "shelter"}), self.sim.world.tile(agent.position).shelter_owner == agent.id
        if sid == "share-water":
            water = self.nearest_known_terrain(agent, Terrain.RIVER)
            recipient = next((other for other in self.sim.agents if other.id != agent.id and other.alive), None)
            if water is None or recipient is None:
                return ToolCall("observe_surroundings"), True
            if step.evidence:
                return ToolCall("observe_surroundings"), True
            return ToolCall("communicate", {"agent_id": recipient.id, "message": f"river:{water[0]},{water[1]}"}), False
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
            return self.nearest_known_terrain(agent, Terrain.RIVER)
        if target == "shelter":
            return self._known_shelter(agent)
        return self._nearest_resource(agent, {"food": "berries"}.get(target, target))

    def _nearest_resource(self, agent: Survivor, resource: str) -> Position | None:
        positions = [pos for pos in agent.known_resources.get(resource, set()) if self.sim.world.tile(pos).resources[resource] > 0]
        return min(positions, key=lambda pos: self.sim.distance(agent.position, pos), default=None)

    def nearest_known_terrain(self, agent: Survivor, terrain: Terrain) -> Position | None:
        positions = [pos for pos, known in agent.known_tiles.items() if known == terrain]
        return min(positions, key=lambda pos: self.sim.distance(agent.position, pos), default=None)

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
            if message.text.startswith("river:"):
                coordinates = message.text.removeprefix("river:").split(",")
                try:
                    position = int(coordinates[0]), int(coordinates[1])
                except (ValueError, IndexError):
                    continue
                if self.sim.world.in_bounds(position):
                    agent.known_tiles[position] = Terrain.RIVER
                    self.sim.trace.record("memory.updated", {"tick": self.sim.tick, "agent": agent.id, "fact": "river", "position": position, "source": message.sender})

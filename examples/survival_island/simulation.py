"""Deterministic, headless survival-island simulation.

The world owns state transitions.  Agents may choose only from registered tools;
they never mutate world state directly.  This keeps a future model-backed policy
behind the same boundary as the deterministic reference policy used here.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
import random
from typing import Any, Iterable

from bbagents import FunctionTool, Observation, Plan, PlanStep, StepStatus, ToolCall, ToolRegistry
from bbagents.observability import InMemoryTrace


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

    def __init__(self, *, size: int = 30, agents: int = 5, seed: int = 7) -> None:
        self.world = Island(size, seed)
        self.random = random.Random(seed + 1)
        self.tick = 0
        self.day_length = 80
        self.messages: deque[Message] = deque(maxlen=100)
        self.event_log: deque[str] = deque(maxlen=120)
        self.trace = InMemoryTrace()
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
            FunctionTool("communicate", "Send a message to another agent", lambda agent_id, message: self.communicate(agent, agent_id, message)),
        ])

    def step(self) -> None:
        self.tick += 1
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

    def _apply_needs(self, agent: Survivor) -> None:
        agent.hunger = min(100.0, agent.hunger + 0.55)
        agent.thirst = min(100.0, agent.thirst + 0.8)
        agent.energy = max(0.0, agent.energy - (0.55 if self.is_night else 0.3))
        agent.sheltered = self.world.tile(agent.position).shelter_owner is not None
        if agent.last_action.startswith("Observed"):
            agent.energy = min(100.0, agent.energy + (2.0 if agent.sheltered else 1.2))
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
        return Observation("world", f"Observed {len(seen)} tiles and {len(nearby)} nearby agents", {"tiles": seen, "nearby_agents": nearby})

    def available_actions(self, agent: Survivor) -> list[ToolCall]:
        """Concrete legal calls at this location; message text remains free-form."""
        tile = self.world.tile(agent.position)
        calls = [ToolCall("observe_surroundings")]
        for direction, (dx, dy) in self.DIRECTIONS.items():
            if self.world.passable((agent.position[0] + dx, agent.position[1] + dy)):
                calls.append(ToolCall("move", {"direction": direction}))
        for resource in Resource:
            if tile.resources[resource.value] > 0:
                calls.append(ToolCall("gather", {"resource": resource.value}))
        for item, recipe in {"axe": {"wood": 3, "stone": 1}, "ration": {"berries": 2}}.items():
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
        agent.energy = max(0.0, agent.energy - 0.35)
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
        agent.energy = max(0.0, agent.energy - 0.7)
        return Observation("gather", f"gathered {amount} {resource}", {"resource": resource, "amount": amount})

    def craft(self, agent: Survivor, item: str) -> Observation:
        recipes = {"axe": {"wood": 3, "stone": 1}, "ration": {"berries": 2}}
        if item not in recipes:
            raise ValueError(f"unknown recipe: {item}")
        recipe = recipes[item]
        if any(agent.inventory[name] < count for name, count in recipe.items()):
            raise ValueError(f"missing ingredients for {item}")
        for name, count in recipe.items():
            agent.inventory[name] -= count
        agent.inventory[item] += 1
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
        emergency = self._emergency(agent)
        if emergency and agent.plan_reason != emergency:
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
        if agent.energy <= 15:
            return "energy emergency"
        return None

    def _call_for_step(self, agent: Survivor, step: PlanStep) -> tuple[ToolCall, bool]:
        sid = step.id
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
            return ToolCall("observe_surroundings"), agent.energy >= 55
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

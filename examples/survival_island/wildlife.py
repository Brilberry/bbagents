"""Finite, seeded wildlife and tool-gated hunting on the simulation thread."""
from dataclasses import dataclass
import random

from bbagents import Observation
from .animal_movement import WATER_LIMITS, neighbors as animal_neighbors, move as move_animal


SPECIES = {
    "deer": {"count": 5, "health": 50, "meat": 4, "hit": 0.65},
    "boar": {"count": 4, "health": 75, "meat": 5, "hit": 0.8},
    "rabbit": {"count": 8, "health": 18, "meat": 1, "hit": 0.5},
}


@dataclass
class Animal:
    id: str
    kind: str
    position: tuple[int, int]
    health: float
    hunger: float = 15
    behavior: str = "roaming"
    next_move_tick: int = 0
    provoked_until: int = -1
    next_attack_tick: int = 0
    target_id: str | None = None
    thirst: float = 12
    water_route: tuple[tuple[int, int], ...] = ()
    skinned: bool = False

    @property
    def alive(self):
        return self.health > 0


class Wildlife:
    def __init__(self, sim, seed):
        self.sim = sim
        self.movement = random.Random(seed + 801)
        self.hunting = random.Random(seed + 901)
        spawn = random.Random(seed + 1001)
        land = [(x, y) for y, row in enumerate(sim.world.tiles) for x, tile in enumerate(row)
                if sim.world.passable((x, y)) and not tile.terrain.is_water]
        self.animals = [Animal(f"{kind}-{index + 1}", kind, spawn.choice(land), rules["health"])
                        for kind, rules in SPECIES.items() for index in range(rules["count"])]
        for animal in self.animals:
            self.record("wildlife.spawned", animal)

    def record(self, event, animal, **extra):
        self.sim.trace.record(event, {"animal": animal.id, "kind": animal.kind,
            "position": animal.position, "health": animal.health,
            "hunger": animal.hunger, "thirst": animal.thirst,
            "behavior": animal.behavior, **extra})

    def visible(self, actor):
        return [animal for animal in self.animals if animal.alive
                and self.sim.distance(actor.position, animal.position) <= actor.visibility]

    def can_hunt(self, actor, animal):
        return (actor.alive and not actor.sleep_remaining and actor.energy >= 4
                and actor.inventory["bow"] > 0 and "hunting" in actor.learned_skills
                and self.sim.tick >= actor.next_hunt_tick and animal in self.visible(actor)
                and self.sim.distance(actor.position, animal.position) <= 3)

    def threats(self, actor):
        if self.sim.world.tile(actor.position).shelter_owner is not None:
            return []
        return [animal for animal in self.visible(actor) if animal.kind == "boar"
                and animal.target_id == actor.id and self.sim.tick <= animal.provoked_until]

    def hunt(self, actor, animal_id):
        animal = next((item for item in self.animals if item.id == animal_id), None)
        if animal is None or not self.can_hunt(actor, animal):
            raise ValueError("hunt requires learned hunting, a bow, four energy, a ready shot and visible prey within three tiles")
        actor.energy -= 4
        actor.next_hunt_tick = self.sim.tick + 4
        hit = self.hunting.random() < SPECIES[animal.kind]["hit"]
        damage = min(animal.health, 16 + actor.strength * 2) if hit else 0
        animal.health -= damage
        animal.provoked_until = self.sim.tick + 12
        animal.target_id = actor.id
        meat = SPECIES[animal.kind]["meat"] if not animal.alive else 0
        actor.inventory["meat"] += meat
        animal.behavior = "dead" if meat else "threatened"
        self.record("wildlife.hunted", animal, agent=actor.id, hit=hit, damage=damage, meat=meat)
        if meat:
            actor.hunting_kills += 1
            self.record("wildlife.killed", animal, agent=actor.id, meat=meat)
        return Observation("hunt", f"{'killed' if meat else 'hit' if hit else 'missed'} {animal.kind}",
                           {"animal": animal.id, "hit": hit, "damage": damage, "meat": meat})

    def update(self):
        water_distances = self.sim._water_distances() if any(animal.alive and animal.thirst >= 54.9 for animal in self.animals) else {}
        for animal in self.animals:
            if not animal.alive:
                continue
            animal.hunger = min(100, animal.hunger + 0.08)
            animal.thirst = min(100, animal.thirst + 0.06)
            neighbors = animal_neighbors(self.sim.world, animal)
            target = next((actor for actor in self.sim.agents if actor.id == animal.target_id and actor.alive
                           and self.sim.world.tile(actor.position).shelter_owner is None), None)
            provoked = target is not None and self.sim.tick <= animal.provoked_until
            if animal.kind == "boar" and provoked and self.sim.distance(target.position, animal.position) <= 1:
                if self.sim.tick >= animal.next_attack_tick:
                    damage = min(target.health, 10)
                    target.health -= damage
                    target.alive = target.health > 0
                    if not target.alive:
                        target.death_cause = f"boar attack ({animal.id})"
                    if target.sleep_remaining:
                        self.sim._wake(target, "boar attack", interrupted=True)
                    animal.next_attack_tick = self.sim.tick + 5
                    self.record("wildlife.attacked", animal, agent=target.id, damage=damage)
            if self.sim.tick < animal.next_move_tick:
                continue
            animal.next_move_tick = self.sim.tick + (2 if animal.kind == "rabbit" else 3)
            threats = [actor.position for actor in self.sim.agents if actor.alive
                       and self.sim.distance(actor.position, animal.position) <= 2]
            threats += [predator.position for predator in self.sim.predators if predator.alive
                        and self.sim.distance(predator.position, animal.position) <= 3]
            tile = self.sim.world.tile(animal.position)
            if provoked and animal.kind == "boar" and neighbors:
                destination = min(neighbors, key=lambda pos: self.sim.distance(pos, target.position)) if self.sim.distance(target.position, animal.position) > 1 else animal.position
                move_animal(self.sim.world, animal, destination)
                animal.behavior = "defending"
            elif threats and neighbors and animal.thirst < 85:
                scores = {pos: min(self.sim.distance(pos, threat) for threat in threats) for pos in neighbors}
                best = max(scores.values())
                move_animal(self.sim.world, animal, self.movement.choice([pos for pos in neighbors if scores[pos] == best]))
                animal.behavior = "fleeing"
            elif animal.thirst >= 55:
                distance = water_distances.get(animal.position, float("inf"))
                if distance == 1 or (distance == 0 and WATER_LIMITS[animal.kind] > 0):
                    animal.thirst = max(0, animal.thirst - 65)
                    animal.behavior = "drinking"
                    self.record("wildlife.drank", animal)
                else:
                    reachable = [pos for pos in neighbors if pos in water_distances]
                    if reachable:
                        move_animal(self.sim.world, animal, min(reachable, key=water_distances.get))
                        animal.behavior = "seeking water"
            elif animal.hunger >= 35 and tile.resources["berries"]:
                tile.resources["berries"] -= 1
                animal.hunger = max(0, animal.hunger - 25)
                animal.behavior = "feeding"
                self.record("wildlife.fed", animal, resource="berries", amount=1)
            elif neighbors:
                food = [pos for pos in neighbors if self.sim.world.tile(pos).resources["berries"]]
                move_animal(self.sim.world, animal, self.movement.choice(food if animal.hunger >= 35 and food else neighbors))
                animal.behavior = "foraging" if animal.hunger >= 35 else "roaming"
            self.record("wildlife.state", animal)

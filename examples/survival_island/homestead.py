"""Actor-authorized homestead activities, with explicit material/time costs."""
from dataclasses import dataclass

from bbagents import Action, ActionRuntime, AuthorizationDecision, FunctionTool, Observation, ToolCall
from bbagents.memory import MemoryRecord


@dataclass
class Plot:
    position: tuple[int, int]
    home: tuple[int, int]
    planted_by: str
    progress: float = 0
    dry_ticks: int = 0


@dataclass
class Fire:
    position: tuple[int, int]
    owner: str
    fuel: int = 80


@dataclass
class Reservoir:
    capacity: int = 10
    water: int = 0


class HomesteadAuthorization:
    def __init__(self, world):
        self.world = world

    def decide(self, action):
        actor = next((agent for agent in self.world.sim.agents if agent.id == action.metadata.get("actor")), None)
        if actor is None or not actor.alive or actor.sleep_remaining:
            return AuthorizationDecision.deny("activity requires a living, awake actor")
        if action.name not in self.world.descriptions:
            return AuthorizationDecision.deny("unknown homestead capability")
        return AuthorizationDecision.allow("actor-scoped homestead capability")


class Homestead:
    descriptions = {"plant": "Plant vegetables next to your shelter using two berries, one wood and one filled flask",
                    "harvest": "Harvest a mature home vegetable plot",
                    "light_fire": "Light a fire with wood/stone, or refuel an existing fire",
                    "cook": "Roast one raw fish or meat at a nearby burning fire",
                    "skin": "Skin an adjacent carcass with a stone knife; once per animal",
                    "tan_hide": "Process one hide into leather at home using wood and stored water",
                    "equip": "Equip a leather garment for partial cold protection",
                    "install_reservoir": "Install a crafted reservoir at your home shelter",
                    "fill_reservoir": "Empty one filled flask into your home reservoir",
                    "drink_reservoir": "Drink one unit from your home reservoir"}

    def __init__(self, sim):
        self.sim = sim
        self.plots = {}
        self.fires = {}
        self.reservoirs = {}
        self.runtime = ActionRuntime({name: self.handler(name) for name in self.descriptions},
                                     policy=HomesteadAuthorization(self))

    def handler(self, name):
        def call(actor, **args):
            survivor = next(agent for agent in self.sim.agents if agent.id == actor)
            return getattr(self, "do_" + name)(survivor, **args)
        return call

    def execute(self, survivor, name, **args):
        actor = survivor
        if name not in self.descriptions:
            raise ValueError("unknown homestead tool")
        expected = {"item"} if name in {"cook", "equip"} else {"animal_id"} if name == "skin" else set()
        if set(args) != expected or any(not isinstance(value, str) for value in args.values()):
            raise ValueError("invalid homestead tool arguments")
        result = self.runtime.execute(Action(name, {"actor": actor.id, **args},
            metadata={"actor": actor.id}), retries=0)
        self.sim.trace.record("homestead.action", {"agent": actor.id, "tool": name,
            "status": result.status.value, "attempts": result.attempts})
        if not result.success:
            raise ValueError(str(result.error))
        return result.output

    def tools_for(self, actor):
        def bind(name):
            def call(**args):
                if set(args) - {"item", "animal_id"}:
                    raise ValueError("invalid homestead tool arguments")
                return self.execute(actor, name, **args)
            return call
        return [FunctionTool(name, description, bind(name)) for name, description in self.descriptions.items()]

    def record(self, actor, event, **data):
        self.sim.trace.record(event, {"agent": actor.id, "position": actor.position, **data})
        actor.memory.remember(MemoryRecord(f"{event}: {data}", source="homestead",
                                           metadata={"tick": self.sim.tick, **data}))

    def skill(self, actor, identity):
        if identity not in actor.learned_skills:
            raise ValueError(f"{identity} skill has not been learned")

    def spend(self, actor, items, energy=2):
        if actor.energy < energy or any(actor.inventory[name] < count for name, count in items.items()):
            raise ValueError("missing ingredients or energy")
        for name, count in items.items():
            actor.inventory[name] -= count
        actor.energy -= energy

    def access(self, actor, home):
        return self.sim.home_is_valid(actor) and actor.home == home

    def plant_site(self, actor):
        tile = self.sim.world.tile(actor.position)
        return (self.sim.home_is_valid(actor) and self.sim.distance(actor.position, actor.home) == 1
                and tile.terrain.value == "grass" and tile.shelter_owner is None
                and actor.position not in self.plots and not self.sim.social.project_at(actor.position)
                and not any(plot.home == actor.home for plot in self.plots.values()))

    def do_plant(self, actor):
        self.skill(actor, "gardening")
        if not self.plant_site(actor) or self.sim.conditions.season == "winter":
            raise ValueError("planting needs empty grass beside your home, outside winter")
        self.spend(actor, {"berries": 2, "wood": 1, "water": 1}, 3)
        actor.inventory["flask"] += 1
        self.plots[actor.position] = Plot(actor.position, actor.home, actor.id)
        self.record(actor, "garden.planted", home=actor.home)
        return Observation("plant", "planted vegetables", {"home": actor.home})

    def do_harvest(self, actor):
        plot = self.plots.get(actor.position)
        if not plot or plot.progress < 160 or not self.access(actor, plot.home):
            raise ValueError("no mature accessible vegetable plot here")
        self.spend(actor, {}, 2)
        actor.inventory["vegetables"] += 6
        del self.plots[actor.position]
        self.record(actor, "garden.harvested", amount=6)
        return Observation("harvest", "harvested six vegetables", {"amount": 6})

    def fire_access(self, actor):
        tile = self.sim.world.tile(actor.position)
        return tile.terrain.value in {"grass", "forest"} and (tile.shelter_owner is None or self.access(actor, actor.position))

    def warmth(self, position):
        return 8 if any(fire.fuel > 0 and self.sim.distance(position, fire.position) <= 1 for fire in self.fires.values()) else 0

    def do_light_fire(self, actor):
        self.skill(actor, "fire")
        if not self.fire_access(actor):
            raise ValueError("fire needs dry land and permission inside a shelter")
        fire = self.fires.get(actor.position)
        self.spend(actor, {"wood": 1} if fire else {"wood": 3, "stone": 2}, 3)
        if fire:
            fire.fuel = min(120, fire.fuel + 40)
        else:
            self.fires[actor.position] = Fire(actor.position, actor.id)
        self.record(actor, "fire.lit", fuel=self.fires[actor.position].fuel)
        return Observation("light_fire", "fire burning", {"fuel": self.fires[actor.position].fuel})

    def do_cook(self, actor, item):
        self.skill(actor, "cooking")
        if item not in {"fish", "meat"} or not self.warmth(actor.position):
            raise ValueError("cooking requires raw fish/meat and a nearby burning fire")
        self.spend(actor, {item: 1}, 2)
        cooked = "cooked_" + item
        actor.inventory[cooked] += 1
        self.record(actor, "food.cooked", raw=item, cooked=cooked)
        return Observation("cook", f"roasted {item}", {"item": cooked})

    def carcasses(self, actor):
        return [animal for animal in self.sim.wildlife.animals if not animal.alive and not animal.skinned
                and self.sim.distance(actor.position, animal.position) <= actor.visibility]

    def do_skin(self, actor, animal_id):
        self.skill(actor, "skinning")
        animal = next((animal for animal in self.carcasses(actor) if animal.id == animal_id), None)
        if not animal or not actor.inventory["knife"] or self.sim.distance(actor.position, animal.position) > 1:
            raise ValueError("skinning requires an adjacent unskinned carcass and a stone knife")
        self.spend(actor, {}, 3)
        hides = {"deer": 2, "boar": 3, "rabbit": 1}[animal.kind]
        animal.skinned = True
        actor.inventory["hide"] += hides
        self.record(actor, "animal.skinned", animal=animal.id, hides=hides)
        return Observation("skin", f"skinned {animal.kind}", {"hides": hides})

    def do_tan_hide(self, actor):
        self.skill(actor, "leatherworking")
        if not self.access(actor, actor.position):
            raise ValueError("tanning requires your home shelter")
        self.spend(actor, {"hide": 1, "wood": 1, "water": 1}, 3)
        actor.inventory["flask"] += 1
        actor.inventory["leather"] += 1
        self.record(actor, "hide.tanned", amount=1)
        return Observation("tan_hide", "tanned one leather", {"amount": 1})

    def do_equip(self, actor, item):
        if item != "leather_garment" or actor.inventory["leather_garment"] < 1:
            raise ValueError("no usable leather garment")
        actor.leather_equipped = True
        self.record(actor, "clothing.equipped", item=item, warmth=6)
        return Observation("equip", "equipped leather garment", {"warmth": 6})

    def do_install_reservoir(self, actor):
        self.skill(actor, "reservoir")
        if not self.access(actor, actor.position) or actor.position in self.reservoirs:
            raise ValueError("reservoir needs your intact home without an existing reservoir")
        self.spend(actor, {"reservoir": 1}, 3)
        self.reservoirs[actor.position] = Reservoir()
        self.record(actor, "reservoir.installed", capacity=10)
        return Observation("install_reservoir", "installed ten-unit reservoir", {"capacity": 10, "water": 0})

    def own_reservoir(self, actor):
        reservoir = self.reservoirs.get(actor.position)
        if not reservoir or not self.access(actor, actor.position):
            raise ValueError("no accessible home reservoir here")
        return reservoir

    def do_fill_reservoir(self, actor):
        reservoir = self.own_reservoir(actor)
        if reservoir.water >= reservoir.capacity:
            raise ValueError("reservoir is full")
        self.spend(actor, {"water": 1}, 1)
        actor.inventory["flask"] += 1
        reservoir.water += 1
        self.record(actor, "reservoir.filled", water=reservoir.water, capacity=reservoir.capacity)
        return Observation("fill_reservoir", "stored one flask of water", {"water": reservoir.water})

    def do_drink_reservoir(self, actor):
        reservoir = self.own_reservoir(actor)
        if reservoir.water < 1 or actor.thirst <= 0:
            raise ValueError("reservoir is empty or survivor is not thirsty")
        reservoir.water -= 1
        actor.thirst = max(0, actor.thirst - 50)
        self.record(actor, "reservoir.drank", water=reservoir.water, thirst=actor.thirst)
        return Observation("drink_reservoir", "drank stored shelter water", {"water": reservoir.water, "thirst": actor.thirst})

    def destroy_home(self, position):
        reservoir = self.reservoirs.pop(position, None)
        fire = self.fires.pop(position, None)
        plots = [pos for pos, plot in self.plots.items() if plot.home == position]
        for pos in plots:
            del self.plots[pos]
        self.sim.trace.record("homestead.destroyed", {"position": position,
            "lost_water": reservoir.water if reservoir else 0,
            "lost_fire_fuel": fire.fuel if fire else 0, "lost_plots": plots})

    def update(self):
        for position, fire in list(self.fires.items()):
            exposed = self.sim.world.tile(position).shelter_owner is None
            fire.fuel -= 2 if exposed and self.sim.conditions.weather in {"rainy", "snowy"} else 1
            if fire.fuel <= 0:
                del self.fires[position]
                self.sim.trace.record("fire.expired", {"agent": fire.owner, "position": position})
        for position, plot in list(self.plots.items()):
            climate = self.sim.conditions
            if climate.temperature < -5:
                del self.plots[position]
                self.sim.trace.record("garden.failed", {"agent": plot.planted_by, "position": position,
                                                        "cause": "frost"})
                continue
            if climate.temperature > 32 and climate.weather != "rainy":
                plot.dry_ticks += 1
                if plot.dry_ticks >= 160:
                    del self.plots[position]
                    self.sim.trace.record("garden.failed", {"agent": plot.planted_by, "position": position,
                                                            "cause": "heat drought"})
                    continue
            elif climate.weather == "rainy":
                plot.dry_ticks = 0
            rate = {"spring": 1, "summer": 1, "autumn": 0.5, "winter": 0}[climate.season]
            if climate.weather == "rainy" and rate:
                rate += 0.5
            plot.progress = min(160, plot.progress + rate)

    def available(self, actor):
        calls = []
        if self.access(actor, actor.position):
            reservoir = self.reservoirs.get(actor.position)
            if not reservoir and "reservoir" in actor.learned_skills and actor.inventory["reservoir"] and actor.energy >= 3:
                calls.append(ToolCall("install_reservoir"))
            if reservoir:
                if reservoir.water < reservoir.capacity and actor.inventory["water"] and actor.energy >= 1:
                    calls.append(ToolCall("fill_reservoir"))
                if reservoir.water and actor.thirst > 0:
                    calls.append(ToolCall("drink_reservoir"))
        if actor.inventory["leather_garment"] and not actor.leather_equipped:
            calls.append(ToolCall("equip", {"item": "leather_garment"}))
        if "skinning" in actor.learned_skills and actor.inventory["knife"] and actor.energy >= 3:
            calls.extend(ToolCall("skin", {"animal_id": animal.id}) for animal in self.carcasses(actor)
                         if self.sim.distance(actor.position, animal.position) <= 1)
        if ("leatherworking" in actor.learned_skills and self.access(actor, actor.position)
                and actor.energy >= 3 and all(actor.inventory[item] >= 1 for item in ("hide", "wood", "water"))):
            calls.append(ToolCall("tan_hide"))
        if "fire" in actor.learned_skills and self.fire_access(actor) and actor.energy >= 3:
            if ((actor.position in self.fires and actor.inventory["wood"] >= 1) or
                    (actor.inventory["wood"] >= 3 and actor.inventory["stone"] >= 2)):
                calls.append(ToolCall("light_fire"))
        if "cooking" in actor.learned_skills and self.warmth(actor.position) and actor.energy >= 2:
            calls.extend(ToolCall("cook", {"item": item}) for item in ("fish", "meat") if actor.inventory[item])
        if ("gardening" in actor.learned_skills and self.plant_site(actor)
                and self.sim.conditions.season != "winter" and actor.energy >= 3
                and actor.inventory["berries"] >= 2 and actor.inventory["wood"] and actor.inventory["water"]):
            calls.append(ToolCall("plant"))
        plot = self.plots.get(actor.position)
        if plot and plot.progress >= 160 and self.access(actor, plot.home) and actor.energy >= 2:
            calls.append(ToolCall("harvest"))
        return calls

    def context(self, actor):
        reservoir = self.reservoirs.get(actor.home) if self.sim.home_is_valid(actor) else None
        return {"home_reservoir": {"water": reservoir.water, "capacity": reservoir.capacity} if reservoir else None,
                "leather_equipped": actor.leather_equipped and actor.inventory["leather_garment"] > 0,
                "carcasses": [{"id": animal.id, "kind": animal.kind, "position": animal.position}
                              for animal in self.carcasses(actor)],
                "fires": [{"position": fire.position, "fuel": fire.fuel}
                          for fire in self.fires.values() if fire.position in actor.known_tiles],
                "plots": [{"position": plot.position, "home": plot.home,
                           "progress": plot.progress, "ready": plot.progress >= 160}
                          for plot in self.plots.values()
                          if plot.position in actor.known_tiles or self.access(actor, plot.home)]}

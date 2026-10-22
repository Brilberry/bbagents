import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Predator, Terrain


def flat_world(agents=1):
    simulation = Simulation(agents=agents, predator_interval=None, lightning_probability=0)
    for row in simulation.world.tiles:
        for tile in row:
            tile.terrain, tile.shelter_owner = Terrain.GRASS, None
    return simulation


def test_full_predator_is_calm_but_defends_itself_when_attacked():
    simulation = flat_world()
    agent = simulation.agents[0]
    wolf = Predator("wolf", "wolf", agent.position, 45, hunger=10)
    simulation.predators = [wolf]
    simulation.tick = 4
    simulation._update_predators()
    assert agent.health == 100
    wolf.position = agent.position
    assert simulation.visible_predators(agent) == [wolf]
    assert simulation.threatening_predators(agent) == []
    assert simulation.policy.defend(agent) is None
    simulation.attack(agent, wolf.id)
    assert simulation.threatening_predators(agent) == [wolf]
    simulation.tick = 8
    simulation._update_predators()
    assert agent.health == 93
    assert wolf.hunger < 11  # Nonfatal attacks do not count as meals.


def test_hunger_increases_hunting_range():
    wolf = Predator("wolf", "wolf", (0, 0), 45)
    ranges = []
    for hunger in (10, 40, 60, 85):
        wolf.hunger = hunger
        ranges.append(wolf.hunting_radius(0))
    assert ranges == [0, 2, 6, 10]


def test_kill_feeds_nearby_packmates_and_stops_hunting():
    simulation = flat_world(agents=2)
    prey, next_survivor = simulation.agents
    prey.health = 1
    next_survivor.position = prey.position
    wolf = Predator("hunter", "wolf", prey.position, 45, "pack")
    friend = Predator("friend", "wolf", prey.position, 45, "pack")
    simulation.predators = [wolf, friend]
    simulation.tick = 4
    simulation._update_predators()
    assert not prey.alive
    assert wolf.hunger < 1 and friend.hunger < 20
    assert next_survivor.health == 100
    assert len(simulation.trace.events_named("predator.fed")) == 2
    simulation.tick = 8
    simulation._update_predators()
    assert next_survivor.health == 100


def test_thirst_drives_path_to_shore_and_drinking_without_survivors():
    simulation = flat_world(agents=0)
    simulation.world.tile((5, 5)).terrain = Terrain.LAKE
    simulation.world.tile((5, 6)).terrain = Terrain.MOUNTAIN
    wolf = Predator("wolf", "wolf", (6, 6), 45, hunger=10, thirst=70)
    simulation.predators = [wolf]
    simulation.tick = 4
    simulation._update_predators()
    assert wolf.position == (6, 5)  # Reachable shore, not the obstructed route.
    assert wolf.behavior == "seeking water"
    simulation.tick = 8
    simulation._update_predators()
    assert wolf.behavior == "drinking"
    assert wolf.thirst < 6
    assert simulation.trace.events_named("predator.drank")
    assert simulation.world.tile(wolf.position).terrain != Terrain.LAKE


def test_urgent_thirst_takes_priority_over_adjacent_prey():
    simulation = flat_world()
    agent = simulation.agents[0]
    agent.position = (5, 5)
    simulation.world.tile((9, 5)).terrain = Terrain.LAKE
    wolf = Predator("wolf", "wolf", agent.position, 45, hunger=95, thirst=90)
    simulation.predators = [wolf]
    simulation.tick = 4
    simulation._update_predators()
    assert agent.health == 100
    assert wolf.position == (6, 5)
    assert wolf.behavior == "seeking water"


def test_predator_needs_rise_twenty_times_slower_and_only_once_per_tick():
    simulation = flat_world()
    agent = simulation.agents[0]
    wolf = Predator("wolf", "wolf", (0, 0), 45, hunger=12, thirst=12)
    for tick in range(100):
        simulation.tick = tick
        simulation._apply_needs(agent)
        simulation._apply_predator_needs(wolf)
        snapshot = (wolf.hunger, wolf.thirst)
        simulation._apply_predator_needs(wolf)
        assert snapshot == (wolf.hunger, wolf.thirst)
    assert agent.hunger - 12 == pytest.approx(22 * (wolf.hunger - 12))
    assert agent.thirst - 12 == pytest.approx(20 * (wolf.thirst - 12))
    assert wolf.health == 45
    assert wolf.provoked_until == -1


def test_predator_need_damage_is_slow_but_eventually_fatal():
    simulation = flat_world()
    agent = simulation.agents[0]
    agent.hunger = agent.thirst = 100
    wolf = Predator("wolf", "wolf", (0, 0), 45, hunger=100, thirst=100)
    for tick in range(100):
        simulation.tick = tick
        if agent.alive:
            simulation._apply_needs(agent)
        simulation._apply_predator_needs(wolf)
    assert not agent.alive
    assert wolf.alive and wolf.health == pytest.approx(25)
    for tick in range(100, 230):
        simulation.tick = tick
        simulation._apply_predator_needs(wolf)
    assert not wolf.alive
    assert simulation.trace.events_named("predator.died")


def test_predators_can_survive_a_thousand_ticks_without_food_or_water():
    simulation = flat_world(agents=0)
    wolf = Predator("wolf", "wolf", (5, 5), 45)
    simulation.predators = [wolf]
    for tick in range(1, 1001):
        simulation.tick = tick
        simulation._update_predators()
    assert wolf.health == 45
    assert wolf.hunger == pytest.approx(85)
    assert wolf.thirst == pytest.approx(52)


def test_unreachable_water_does_not_cause_errors_or_urgent_thirst_attacks():
    simulation = flat_world()
    agent = simulation.agents[0]
    agent.position = (5, 5)
    simulation.world.tile((15, 15)).terrain = Terrain.LAKE
    for position in ((14, 15), (16, 15), (15, 14), (15, 16)):
        simulation.world.tile(position).terrain = Terrain.MOUNTAIN
    simulation.predators = [Predator("wolf", "wolf", agent.position, 45, hunger=95, thirst=90)]
    simulation.tick = 4
    simulation._update_predators()
    assert agent.health == 100
    assert not simulation.trace.events_named("predator.drank")

import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Predator, Terrain
from examples.survival_island.wildlife import Animal


def test_bear_and_survivor_share_finite_fish():
    sim = Simulation(agents=1, predator_interval=None)
    actor = sim.agents[0]
    actor.position = (10, 10)
    for pos in sim.world.positions_within(actor.position, 1):
        sim.world.tile(pos).terrain = Terrain.GRASS
    lake = sim.world.tile((11, 10))
    lake.terrain, lake.fish_stock = Terrain.LAKE, 2
    sim.learn_skill(actor, "fishing", "teacher")
    actor.inventory["fishing_rod"] = 1
    sim.fish(actor)
    assert lake.fish_stock == 1
    actor.position = (20, 20)
    sim.wildlife.animals.clear()
    bear = Predator("bear", "bear", (10, 10), 150, hunger=70)
    assert sim._bear_wildlife_opportunity(bear)
    assert lake.fish_stock == 0 and bear.hunger == 45
    actor.position = (10, 10)
    sim.tick = 8
    with pytest.raises(ValueError):
        sim.fish(actor)


def test_hungry_bear_pursues_wildlife_when_no_human_is_near():
    sim = Simulation(agents=1, predator_interval=None)
    sim.agents[0].position = (25, 25)
    for row in sim.world.tiles:
        for tile in row:
            tile.terrain = Terrain.GRASS
    sim.wildlife.animals = [Animal("deer", "deer", (8, 5), 50)]
    bear = Predator("bear", "bear", (5, 5), 150, hunger=70, thirst=0)
    sim.predators = [bear]
    sim.tick = 2
    sim._update_predators()
    assert bear.position == (6, 5) and bear.behavior == "hunting wildlife"


def test_lake_fish_replenish_to_capacity_not_infinite():
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    lake = next(tile for row in sim.world.tiles for tile in row if tile.terrain == Terrain.LAKE)
    lake.fish_stock = 0
    sim.tick = 79
    sim.step()
    assert lake.fish_stock == 1


def test_stone_can_be_collected_from_impassable_mountain_edge():
    sim = Simulation(agents=1, predator_interval=None)
    actor = sim.agents[0]
    actor.position = (10, 10)
    sim.world.tile(actor.position).terrain = Terrain.GRASS
    source = (11, 10)
    sim.world.tile(source).terrain = Terrain.MOUNTAIN
    sim.world.tile(source).resources["stone"] = 2
    actor.known_resources["stone"] = {source}
    call, done = sim.policy._collect(actor, "stone", 1)
    assert call.tool_name == "gather" and not done
    sim.tools[actor.id].execute(call)
    assert actor.inventory["stone"] == 1
    assert sim.world.tile(source).resources["stone"] == 1
    assert actor.position == (10, 10)

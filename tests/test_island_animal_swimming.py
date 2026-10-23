import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Predator, Terrain
from examples.survival_island.wildlife import Animal


def channel():
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    for row in sim.world.tiles:
        for tile in row:
            tile.terrain = Terrain.MOUNTAIN
    for x in range(5, 10):
        sim.world.tile((x, 5)).terrain = Terrain.GRASS if x in (5, 9) else Terrain.LAKE
    sim.agents[0].position = (9, 5)
    return sim


@pytest.mark.parametrize("terrain", [Terrain.LAKE, Terrain.RIVER])
def test_bear_crosses_water_to_hunt_but_wolf_cannot(terrain):
    for kind in ("bear", "wolf"):
        sim = channel()
        for x in (6, 7, 8):
            sim.world.tile((x, 5)).terrain = terrain
        predator = Predator("animal", kind, (5, 5), 150, hunger=70, thirst=0)
        sim.predators = [predator]
        for tick in (4, 8, 12):
            sim.tick = tick
            sim._update_predators()
        assert predator.position == ((8, 5) if kind == "bear" else (5, 5))


@pytest.mark.parametrize("kind", ["deer", "boar", "rabbit"])
def test_land_wildlife_cannot_enter_water_even_when_fleeing(kind):
    sim = channel()
    sim.agents[0].position = (5, 5)
    animal = Animal("prey", kind, (5, 5), 50)
    sim.wildlife.animals = [animal]
    for tick in range(12):
        sim.tick = tick
        sim.wildlife.update()
        assert animal.position == (5, 5)


def test_bear_can_drink_while_swimming():
    sim = channel()
    bear = Predator("bear", "bear", (7, 5), 150, hunger=0, thirst=90)
    sim.predators = [bear]
    sim.tick = 2
    sim._update_predators()
    assert bear.behavior == "drinking"
    assert bear.thirst < 30


@pytest.mark.parametrize("kind,limit", [("bear", 3), ("wolf", 1), ("deer", 1), ("boar", 1), ("rabbit", 0)])
def test_crossing_width_limit_and_return_to_land(kind, limit):
    from examples.survival_island.animal_movement import neighbors, move
    for width in (1, 2, 3, 4):
        sim = channel()
        for x in range(5, 11):
            sim.world.tile((x, 5)).terrain = Terrain.MOUNTAIN
        sim.world.tile((5, 5)).terrain = Terrain.GRASS
        for x in range(6, 6 + width):
            sim.world.tile((x, 5)).terrain = Terrain.LAKE
        sim.world.tile((6 + width, 5)).terrain = Terrain.GRASS
        animal = Predator("p", kind, (5, 5), 100) if kind in {"wolf", "bear"} else Animal("a", kind, (5, 5), 50)
        if width > limit:
            assert (6, 5) not in neighbors(sim.world, animal)
        else:
            for x in range(6, 7 + width):
                assert (x, 5) in neighbors(sim.world, animal)
                move(sim.world, animal, (x, 5))
            assert animal.water_route == ()


@pytest.mark.parametrize("kind", ["bear", "wolf", "deer", "boar", "rabbit"])
def test_all_animals_drink_from_shore(kind):
    sim = channel()
    sim.agents[0].position = (20, 20)
    if kind in {"wolf", "bear"}:
        animal = Predator("p", kind, (5, 5), 100, hunger=0, thirst=90)
        sim.predators = [animal]
        sim.tick = 4
        sim._update_predators()
    else:
        animal = Animal("a", kind, (5, 5), 50, thirst=90)
        sim.wildlife.animals = [animal]
        sim.wildlife.update()
    assert animal.behavior == "drinking"
    assert animal.thirst < 30


@pytest.mark.parametrize("kind", ["bear", "wolf", "deer", "boar"])
def test_swimming_animals_drink_inside_water(kind):
    sim = channel()
    sim.agents[0].position = (20, 20)
    if kind in {"wolf", "bear"}:
        animal = Predator("p", kind, (6, 5), 100, hunger=0, thirst=90)
        sim.predators = [animal]
        sim.tick = 4
        sim._update_predators()
    else:
        animal = Animal("a", kind, (6, 5), 50, thirst=90)
        sim.wildlife.animals = [animal]
        sim.wildlife.update()
    assert animal.behavior == "drinking"


def test_hungry_bears_take_nearby_deer_and_rabbits_but_not_boars():
    sim = channel()
    bear = Predator("bear", "bear", (5, 5), 150, hunger=70, thirst=0)
    deer = Animal("deer", "deer", (5, 5), 50)
    rabbit = Animal("rabbit", "rabbit", (5, 5), 18)
    boar = Animal("boar", "boar", (5, 5), 75)
    sim.wildlife.animals = [deer, rabbit, boar]
    assert sim._bear_wildlife_opportunity(bear)
    assert not deer.alive and bear.hunger == 10
    bear.hunger = 70
    assert not sim._bear_wildlife_opportunity(bear)  # Shared attack cooldown.
    sim.tick = 6
    assert sim._bear_wildlife_opportunity(bear)
    assert not rabbit.alive and bear.hunger == 50
    sim.tick = 12
    assert not sim._bear_wildlife_opportunity(bear)
    assert boar.alive
    assert len(sim.trace.events_named("wildlife.killed_by_predator")) == 2


def test_calm_bears_do_not_kill_wildlife():
    sim = channel()
    bear = Predator("bear", "bear", (5, 5), 150, hunger=20)
    sim.wildlife.animals = [Animal("deer", "deer", (5, 5), 50)]
    assert not sim._bear_wildlife_opportunity(bear)
    assert sim.wildlife.animals[0].alive

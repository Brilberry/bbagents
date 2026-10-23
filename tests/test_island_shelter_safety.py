import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Predator, Terrain
from examples.survival_island.wildlife import Animal


@pytest.mark.parametrize("kind", ["bear", "wolf", "boar"])
def test_shelter_blocks_attacks_and_entry_while_sleeping(kind):
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = sim.agents[0]
    agent.position = (10, 10)
    agent.home = agent.position
    for pos in ((10, 10), (9, 10), (11, 10), (10, 9), (10, 11)):
        sim.world.tile(pos).terrain = Terrain.GRASS
    sim.world.tile(agent.position).shelter_owner = agent.id
    sim.wildlife.animals.clear()
    if kind == "boar":
        animal = Animal("boar", kind, (9, 10), 75, target_id=agent.id, provoked_until=100)
        sim.wildlife.animals = [animal]
    else:
        animal = Predator("predator", kind, (9, 10), 150, hunger=90, thirst=0)
        sim.predators = [animal]
    sim.sleep(agent)
    for _ in range(8):
        sim.step()
        assert animal.position != agent.position
        assert agent.health == 100
    assert agent.sleep_remaining == 4
    assert not sim.trace.events_named("predator.attacked")
    assert not sim.trace.events_named("wildlife.attacked")


def test_destroyed_shelter_no_longer_blocks_attack():
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = sim.agents[0]
    sim.wildlife.animals.clear()
    sim.world.tile(agent.position).shelter_owner = agent.id
    bear = Predator("bear", "bear", agent.position, 150, hunger=90, thirst=0)
    sim.predators = [bear]
    sim.tick = 2
    sim._update_predators()
    assert agent.health == 100
    sim.lightning_strike(agent.position)
    bear.position = agent.position
    sim.tick = 4
    sim._update_predators()
    assert agent.health == 70

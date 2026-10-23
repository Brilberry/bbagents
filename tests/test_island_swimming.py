import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Terrain


@pytest.mark.parametrize("terrain", [Terrain.LAKE, Terrain.RIVER])
def test_contiguous_water_slows_movement_and_traces_waits(terrain):
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    agent.position = (1, 1)
    for x in range(1, 6):
        sim.world.tile((x, 1)).terrain = terrain if x in (2, 3, 4) else Terrain.GRASS
    sim.move(agent, "east")
    assert agent.position == (2, 1)
    sim.tick += 1
    result = sim.move(agent, "east")
    assert result.content.startswith("swam")
    assert agent.position == (3, 1)
    ready = agent.next_move_tick
    hunger = agent.hunger
    sim.step()
    assert agent.position == (3, 1)
    assert agent.last_action == "swimming through open water"
    assert agent.hunger > hunger
    sim.move(agent, "east")
    assert agent.position == (3, 1)
    sim.step()
    assert agent.position == (3, 1)
    sim.tick = ready
    sim.move(agent, "east")
    assert agent.position == (4, 1)
    sim.tick = agent.next_move_tick
    sim.move(agent, "east")
    assert agent.position == (5, 1)
    assert agent.next_move_tick == sim.tick


def test_single_cell_river_crossing_has_no_swimming_delay():
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    agent.position = (1, 1)
    for x in range(1, 4):
        sim.world.tile((x, 1)).terrain = Terrain.RIVER if x == 2 else Terrain.GRASS
    sim.move(agent, "east")
    sim.tick += 1
    sim.move(agent, "east")
    assert agent.position == (3, 1)

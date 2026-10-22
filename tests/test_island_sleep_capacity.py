import pytest

from bbagents import PlanStep
from examples.survival_island import Simulation
from examples.survival_island.simulation import Terrain


def test_only_one_sleeper_per_shelter_and_waking_releases_capacity():
    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    first, second = simulation.agents
    second.position = first.position
    simulation.world.tile(first.position).shelter_owner = first.id
    simulation.sleep(first)
    assert not any(call.tool_name == "sleep" for call in simulation.available_actions(second))
    with pytest.raises(ValueError, match="already has a sleeping survivor"):
        simulation.sleep(second)
    assert second.sleep_remaining == 0
    simulation._wake(first, "predator", interrupted=True)
    simulation.sleep(second)
    assert second.sleep_remaining == 12


@pytest.mark.parametrize("ending", ["completed", "death"])
def test_shelter_capacity_is_released_on_completion_or_death(ending):
    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    first, second = simulation.agents
    second.position = first.position
    simulation.world.tile(first.position).shelter_owner = first.id
    simulation.sleep(first)
    if ending == "completed":
        for _ in range(12):
            simulation._advance_sleep(first)
    else:
        first.alive = False
        simulation._wake(first, "death", interrupted=True)
    simulation.sleep(second)
    assert second.sleep_remaining == 12


@pytest.mark.parametrize("sheltered,hp,energy", [(False, 1, 2.5), (True, 2, 5)])
def test_sleep_recovers_health_and_energy_and_freezes_needs(sheltered, hp, energy):
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    simulation.world.tile(agent.position).shelter_owner = agent.id if sheltered else None
    agent.health, agent.energy = 40, 0
    agent.hunger, agent.thirst = 79, 79
    simulation.sleep(agent)
    for _ in range(11):
        simulation.step()
    assert agent.sleep_remaining == 1
    assert agent.hunger == agent.thirst == 79
    assert agent.health == 40 + 11 * hp
    assert agent.energy == 11 * energy
    simulation._advance_sleep(agent)
    assert not agent.sleep_remaining
    simulation._apply_needs(agent)
    assert agent.hunger > 79 and agent.thirst > 79


def test_outdoors_allows_multiple_sleepers_and_recovery_is_capped():
    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    first, second = simulation.agents
    second.position = first.position
    simulation.world.tile(first.position).shelter_owner = None
    for agent in simulation.agents:
        simulation.sleep(agent)
        simulation._advance_sleep(agent)
        assert agent.health == agent.energy == 100
        assert agent.sleep_remaining == 11


def test_night_policy_avoids_occupied_shelter_and_handles_late_occupancy():
    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    first, second = simulation.agents
    first.position = second.position = (10, 10)
    for x, y in ((10, 10), (11, 10), (9, 10), (10, 11), (10, 9)):
        simulation.world.tile((x, y)).terrain = Terrain.GRASS
    simulation.world.tile(first.position).shelter_owner = first.id
    spare = (11, 10)
    simulation.world.tile(spare).shelter_owner = second.id
    second.known_tiles = {first.position: Terrain.GRASS, spare: Terrain.GRASS}
    simulation.sleep(first)
    call, done = simulation.policy._call_for_step(second, PlanStep("sleep-site", "Find bed"))
    assert not done
    assert call.tool_name == "move"
    simulation.tools[second.id].execute(call)
    assert second.position == spare
    second.position = first.position  # Occupied after completing the earlier site step.
    call, done = simulation.policy._call_for_step(second, PlanStep("sleep", "Sleep"))
    assert call.tool_name == "move" and not done
    simulation.tools[second.id].execute(call)
    assert simulation.sleep_site_available(second, second.position)

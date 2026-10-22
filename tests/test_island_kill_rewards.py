import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Predator


@pytest.mark.parametrize("kind,food,hydration", [("wolf", 30, 10), ("bear", 50, 20)])
def test_killing_predator_reduces_killers_needs_once(kind, food, hydration):
    simulation = Simulation(agents=2, predator_interval=None)
    agent, other = simulation.agents
    agent.inventory["spear"] = 1
    agent.hunger = agent.thirst = 80
    predator = Predator("prey", kind, agent.position, 25)
    simulation.predators = [predator]
    other_needs = (other.hunger, other.thirst)
    result = simulation.attack(agent, predator.id)
    assert not predator.alive
    assert agent.hunger == 80 - food
    assert agent.thirst == 80 - hydration
    assert (other.hunger, other.thirst) == other_needs
    assert result.metadata["hunger_recovered"] == food
    assert result.metadata["thirst_recovered"] == hydration
    assert "recovered" in result.content
    simulation.tick += 3
    with pytest.raises(ValueError):
        simulation.attack(agent, predator.id)
    assert agent.hunger == 80 - food
    assert len(simulation.trace.events_named("survivor.kill_reward")) == 1


def test_nonfatal_hits_do_not_feed_or_hydrate():
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    simulation.predators = [Predator("prey", "bear", agent.position, 150)]
    before = (agent.hunger, agent.thirst)
    result = simulation.attack(agent, "prey")
    assert (agent.hunger, agent.thirst) == before
    assert "hunger_recovered" not in result.metadata
    assert not simulation.trace.events_named("survivor.kill_reward")


def test_kill_reward_clamps_needs_and_reports_actual_recovery():
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    agent.hunger, agent.thirst = 3, 2
    simulation.predators = [Predator("prey", "wolf", agent.position, 1)]
    result = simulation.attack(agent, "prey")
    assert agent.hunger == agent.thirst == 0
    assert result.metadata["hunger_recovered"] == 3
    assert result.metadata["thirst_recovered"] == 2

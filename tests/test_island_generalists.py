import pytest

from examples.survival_island import Simulation
from examples.survival_island.model_policy import ModelPolicy
from examples.survival_island.simulation import Terrain


@pytest.mark.parametrize("seed", [0, 7, 11, 19])
def test_survivors_start_separated_with_no_knowledge_of_each_other(seed):
    simulation = Simulation(seed=seed)
    for agent in simulation.agents:
        assert not agent.known_survivors
        assert not agent.inbox
        assert all(simulation.distance(agent.position, other.position) > agent.visibility
                   for other in simulation.agents if other is not agent)
    assert not simulation.messages
    assert not simulation.trace.events_named("survivor.discovered")
    toolsets = [{name for name, _ in tools.descriptions()} for tools in simulation.tools.values()]
    assert all(tools == toolsets[0] for tools in toolsets)


def test_every_survivor_can_change_priorities_without_teammates():
    simulation = Simulation(predator_interval=None, lightning_probability=0)
    for agent in simulation.agents:
        agent.known_tiles.clear()
        agent.inventory.clear()
        agent.home = None
        simulation.policy._update_objective(agent)
        assert agent.objective.startswith("Secure")
        agent.known_tiles[(15, 0)] = Terrain.RIVER
        simulation.policy._update_objective(agent)
        assert agent.objective.startswith("Build")
        agent.home = agent.position
        simulation.world.tile(agent.position).shelter_owner = agent.id
        simulation.policy._update_objective(agent)
        assert agent.objective == "Stockpile food for myself"
        agent.inventory["berries"] = 6
        simulation.policy._update_objective(agent)
        assert agent.objective == "Prepare personal defenses"
        agent.inventory["spear"] = 1
        simulation.policy._update_objective(agent)
        assert agent.objective.startswith("Develop")
        agent.inventory["axe"] = 1
        simulation.policy._update_objective(agent)
        assert agent.objective.startswith("Explore")
        agent.home = None
        simulation.policy._update_objective(agent)
        assert agent.objective.startswith("Build")
    assert simulation.trace.events_named("objective.changed")


def test_encounters_unlock_local_communication_not_global_broadcasts():
    simulation = Simulation(agents=2)
    first, second = simulation.agents
    with pytest.raises(ValueError, match="discovered and within visibility"):
        simulation.communicate(first, second.id, "hello")
    second.position = first.position
    simulation.observe(first)
    assert second.id in first.known_survivors
    assert not simulation.messages  # Observation itself never broadcasts water.
    simulation.communicate(first, second.id, "hello")
    assert first.id in second.known_survivors
    assert second.inbox[-1].text == "hello"
    assert any(call.tool_name == "communicate" for call in simulation.available_actions(first))
    encounters = len(simulation.trace.events_named("survivor.discovered"))
    simulation.observe(first)
    assert len(simulation.trace.events_named("survivor.discovered")) == encounters
    second.position = (0, 0) if first.position != (0, 0) else (29, 29)
    if simulation.distance(first.position, second.position) <= first.visibility:
        second.position = (29, 29)
    with pytest.raises(ValueError, match="discovered and within visibility"):
        simulation.communicate(first, second.id, "remote message")
    assert not any(call.tool_name == "communicate" for call in simulation.available_actions(first))


def test_model_context_contains_only_encountered_survivors():
    simulation = Simulation(agents=2)
    first, second = simulation.agents
    policy = ModelPolicy(simulation, model="fake")
    try:
        assert policy._snapshot(first)["encountered_survivors"] == []
        second.position = first.position
        context = policy._snapshot(first)
        assert context["encountered_survivors"] == [{"id": second.id, "last_seen": second.position}]
    finally:
        policy.close()


def test_a_lone_generalist_survives_and_builds_its_own_home():
    simulation = Simulation(agents=1, predator_interval=None, survivor_interval=None,
                            lightning_probability=0)
    for _ in range(1000):
        simulation.step()
    agent = simulation.agents[0]
    assert agent.alive
    assert agent.home is not None
    assert agent.inventory["spear"] == 1
    assert not agent.known_survivors

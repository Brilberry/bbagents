import pytest

from examples.survival_island import Simulation
from examples.survival_island.model_policy import ModelPolicy
from examples.survival_island.simulation import Predator, Terrain


def scene(model=False):
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = sim.agents[0]
    agent.position = (10, 10)
    agent.energy = 0
    agent.health = 70
    agent.hunger = 20
    agent.thirst = 20
    sim.wildlife.animals.clear()
    for pos in sim.world.positions_within(agent.position, 2):
        tile = sim.world.tile(pos)
        tile.terrain = Terrain.GRASS
        tile.shelter_owner = None
    sim.predators = [Predator("wolf", "wolf", (11, 10), 45, hunger=90, thirst=0)]
    if model:
        sim.policy = ModelPolicy(sim, model="fake", requester=lambda *args: pytest.fail("emergency requested inference"))
    return sim, agent


@pytest.mark.parametrize("model", [False, True])
def test_exhausted_defense_recovers_instead_of_observing_forever(model):
    sim, agent = scene(model)
    try:
        health = agent.health
        for _ in range(3):
            call = sim.policy.decide(agent)
            assert call.tool_name == "rest"
            assert sim.action_is_available(call, sim.available_actions(agent))
            sim.rest(agent)
            if agent.energy >= 3:
                break
        assert agent.energy >= 3
        assert agent.health == health  # No healing under threat.
        assert sim.policy.decide(agent).tool_name == "flee"
    finally:
        if model:
            sim.policy.close()


@pytest.mark.parametrize("water", [True, False])
def test_exhausted_thirst_uses_carried_or_adjacent_water(water):
    sim, agent = scene()
    agent.thirst = 95
    if water:
        agent.inventory["water"] = 1
    else:
        sim.world.tile((10, 9)).terrain = Terrain.RIVER
    call = sim.policy.decide(agent)
    assert call.tool_name == ("use_water" if water else "drink")
    if water:
        sim.social.execute(agent, "use_water")
        assert agent.inventory["water"] == 0
        assert agent.inventory["flask"] == 1
    else:
        sim.drink(agent)
    assert agent.thirst < 64


def test_exhausted_hunger_uses_food_before_recovering():
    sim, agent = scene()
    agent.hunger = 95
    agent.inventory["vegetables"] = 1
    call = sim.policy.decide(agent)
    assert call.tool_name == "eat" and call.arguments == {"item": "vegetables"}


def test_recovery_does_not_protect_against_predator_attacks():
    sim, agent = scene()
    sim.rest(agent)
    sim.tick = 4
    before = agent.health
    sim._update_predators()
    assert agent.health < before

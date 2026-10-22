import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Island, Terrain


def lake_components(world, terrain=Terrain.LAKE):
    remaining = {(x, y) for y, row in enumerate(world.tiles)
                 for x, tile in enumerate(row) if tile.terrain == terrain}
    components = []
    while remaining:
        pending = [remaining.pop()]
        component = set(pending)
        while pending:
            x, y = pending.pop()
            for position in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if position in remaining:
                    remaining.remove(position)
                    component.add(position)
                    pending.append(position)
        components.append(component)
    return components


@pytest.mark.parametrize("seed", range(20))
def test_three_separate_lakes_have_walkable_shores(seed):
    world = Island(seed=seed)
    components = lake_components(world)
    assert len(components) == 3
    river = lake_components(world, Terrain.RIVER)
    assert len(river) == 1
    assert {y for x, y in river[0]} == set(range(world.size))
    assert all(13 <= len(component) <= 29 for component in components)
    for component in components:
        assert not any(x in (0, 29) or y in (0, 29) for x, y in component)
        shore = {neighbor for x, y in component
                 for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                 if neighbor not in component}
        assert all(world.tile(position).terrain == Terrain.GRASS for position in shore)
    assert max(x for x, y in world.lake_centers) - min(x for x, y in world.lake_centers) >= 12
    assert max(y for x, y in world.lake_centers) - min(y for x, y in world.lake_centers) >= 12
    same = Island(seed=seed)
    assert [[tile.terrain for tile in row] for row in world.tiles] == [
        [tile.terrain for tile in row] for row in same.tiles]


def test_survivors_drink_from_each_lake_and_share_lake_locations():
    simulation = Simulation(agents=2, predator_interval=None)
    agent, friend = simulation.agents
    for component in lake_components(simulation.world):
        x, y = min(component)
        agent.position = friend.position = (x - 1, y)
        agent.known_tiles.clear()
        agent.thirst = 70
        simulation.observe(agent)
        assert any(call.tool_name == "drink" for call in simulation.available_actions(agent))
        assert "lake" in simulation.drink(agent).content
        assert agent.thirst < 70
        simulation.policy._read_messages(friend)
        assert any(terrain == Terrain.LAKE for terrain in friend.known_tiles.values())
    assert any(message.text.startswith("lake:") for message in simulation.messages)


def test_river_is_drinkable_shared_and_used_by_predator_water_navigation():
    simulation = Simulation(agents=2, predator_interval=None)
    agent, friend = simulation.agents
    river = lake_components(simulation.world, Terrain.RIVER)[0]
    x, y = min(river, key=lambda pos: pos[1])
    agent.position = friend.position = (x - 1, y)
    simulation.world.tile(agent.position).terrain = Terrain.GRASS
    agent.known_tiles.clear()
    agent.thirst = 70
    simulation.observe(agent)
    assert simulation.policy.nearest_known_water(agent) in river
    assert any(call.tool_name == "drink" for call in simulation.available_actions(agent))
    assert "river" in simulation.drink(agent).content
    simulation.policy._read_messages(friend)
    assert any(terrain == Terrain.RIVER for terrain in friend.known_tiles.values())
    assert any(message.text.startswith("river:") for message in simulation.messages)
    assert simulation._water_distances()[agent.position] == 1

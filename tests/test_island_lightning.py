import random
from unittest.mock import Mock

from examples.survival_island import Simulation


def test_shelters_do_not_change_random_strike_locations():
    bare = Simulation(agents=0)
    built = Simulation(agents=0)
    bare.weather_random = random.Random(19)
    built.weather_random = random.Random(19)
    for y, row in enumerate(built.world.tiles):
        for x, tile in enumerate(row):
            if (x + y) % 3 == 0:
                tile.shelter_owner = "test-owner"
    for _ in range(500):
        first = bare.lightning_strike().metadata["position"]
        second = built.lightning_strike().metadata["position"]
        assert first == second


def test_random_targeting_can_hit_every_tile_including_water_and_shelters():
    simulation = Simulation(agents=0)
    size = simulation.world.size
    coordinates = [(x, y) for y in range(size) for x in range(size)]
    simulation.world.tile((0, 0)).shelter_owner = "test-owner"
    simulation.weather_random = Mock()
    simulation.weather_random.randrange.side_effect = [value for pos in coordinates for value in pos]
    for position in coordinates:
        assert simulation.lightning_strike().metadata["position"] == position
    assert simulation.weather_random.randrange.call_count == 2 * size * size
    assert all(call.args == (size,) for call in simulation.weather_random.randrange.call_args_list)
    simulation.weather_random.random.assert_not_called()
    simulation.weather_random.choice.assert_not_called()

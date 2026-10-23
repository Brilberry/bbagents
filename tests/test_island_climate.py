from dataclasses import replace

import pytest

from bbagents import ToolCall
from examples.survival_island import Simulation
from examples.survival_island.climate import Climate, Conditions, SEASONS
from examples.survival_island.competition import validate_competition


def test_default_and_northern_season_order_and_repeat():
    climate = Climate(17)
    assert [climate.at(day * 80, 80).season for day in (0, 10, 20, 30, 40)] == [*SEASONS, "spring"]
    assert Climate(17, "winter").at(800, 80).season == "spring"


def test_weather_is_seeded_and_season_appropriate():
    for season in SEASONS:
        climate = Climate(42, season)
        conditions = [climate.at(day * 80, 80) for day in range(10)]
        assert conditions == [Climate(42, season).at(day * 80, 80) for day in range(10)]
        assert all(c.weather in {"sunny", "rainy", "snowy"} for c in conditions)
        if season != "winter":
            assert all(c.weather != "snowy" for c in conditions)
        if season != "summer":
            assert all(not c.heatwave for c in conditions)
    winters = [Climate(seed, "winter").at(0, 80) for seed in range(100)]
    summers = [Climate(seed, "summer").at(0, 80) for seed in range(100)]
    assert any(c.weather == "snowy" for c in winters)
    assert any(c.heatwave and c.temperature >= 36 for c in summers)
    spring = [Climate(seed).at(60, 80) for seed in range(100)]
    assert all(5 <= c.temperature < 32 and not c.heatwave for c in spring)


def test_nights_are_cooler_and_survivor_rng_does_not_change_climate():
    climate = Climate(17)
    assert climate.at(60, 80).temperature == pytest.approx(climate.at(0, 80).temperature - 5)
    a, b = Simulation(seed=17), Simulation(seed=17)
    for _ in range(100):
        a.random.random()
    assert [a.climate.at(tick, 80) for tick in range(0, 3200, 80)] == [b.climate.at(tick, 80) for tick in range(0, 3200, 80)]


def test_starting_season_competition_validation_and_default():
    from pathlib import Path
    from examples.survival_island.competition import load
    rules = load(Path(__file__).parents[1] / "examples/survival_island/configs/competition.json")
    assert validate_competition({**rules, "starting_season": "winter"})["starting_season"] == "winter"
    rules.pop("starting_season")
    assert validate_competition(rules)["starting_season"] == "spring"
    for invalid in ("monsoon", 1, None):
        with pytest.raises(ValueError, match="starting_season"):
            validate_competition({**rules, "starting_season": invalid})


def test_heat_increases_thirst_and_shelter_protects():
    sim = Simulation(agents=2, predator_interval=None)
    outside, inside = sim.agents
    sim.world.tile(outside.position).shelter_owner = None
    sim.world.tile(inside.position).shelter_owner = inside.id
    sim.conditions = Conditions("summer", "sunny", 39, heatwave=True)
    for agent in sim.agents:
        agent.thirst = 0
        sim._apply_needs(agent)
    assert outside.thirst > inside.thirst > 0.8


def test_cold_accumulates_and_can_kill_but_shelter_protects():
    sim = Simulation(agents=2, predator_interval=None)
    outside, inside = sim.agents
    sim.world.tile(outside.position).shelter_owner = None
    sim.world.tile(inside.position).shelter_owner = inside.id
    sim.conditions = Conditions("winter", "snowy", -10)
    outside.health = 0.1
    for _ in range(60):
        for agent in sim.agents:
            if agent.alive:
                agent.hunger = agent.thirst = 0
                agent.energy = 100
                sim._apply_needs(agent)
    assert not outside.alive and outside.death_cause == "hypothermia"
    assert inside.health == 100 and inside.cold_exposure == 0


def test_sleep_does_not_freeze_cold_damage_or_heal_hypothermia():
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    sim.world.tile(agent.position).shelter_owner = None
    sim.conditions = Conditions("winter", "snowy", -10)
    agent.cold_exposure = 80
    agent.health = 50
    sim.sleep(agent)
    sim._apply_needs(agent)
    health = agent.health
    sim._advance_sleep(agent)
    assert agent.health == health < 50
    sim.conditions = replace(sim.conditions, temperature=20, weather="sunny")
    exposure = agent.cold_exposure
    sim._apply_needs(agent)
    assert agent.cold_exposure < exposure


def test_bad_weather_policy_rests_in_permitted_shelter_and_needs_still_override():
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    agent.home = agent.position
    sim.world.tile(agent.position).shelter_owner = agent.id
    agent.known_tiles[agent.position] = sim.world.tile(agent.position).terrain
    sim.conditions = Conditions("winter", "snowy", -8)
    assert sim.policy.weather_response(agent) == ToolCall("rest")
    sim.policy.decide(agent)
    assert agent.decision_source == "weather policy"
    agent.thirst = 90
    sim.policy.decide(agent)
    assert agent.decision_source != "weather policy" or agent.plan_reason == "thirst emergency"

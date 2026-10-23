from copy import deepcopy
from pathlib import Path

import pytest

from examples.survival_island.competition import (
    compete, create_simulation, load, score, validate_competition, validate_team,
)

CONFIG = Path(__file__).parents[1] / "examples/survival_island/configs"


def settings():
    return load(CONFIG / "team.json5", json5=True), load(CONFIG / "competition.json")


def test_example_and_defaults_and_unequal_budgets():
    team, rules = settings()
    normalized = validate_team(team, rules)
    assert [sum(row["attributes"].values()) for row in normalized["survivors"]] == [20, 20, 20, 18, 22]
    assert normalized["survivors"][1]["behavior"]["thresholds"]["hunger"] == 6
    assert "behavior" not in team["survivors"][1]


@pytest.mark.parametrize("field,value", [("intelligence", 0), ("strength", 11), ("endurance", True), ("perception", 2.5)])
def test_attributes_reject_invalid_values(field, value):
    team, rules = settings()
    team["survivors"][0]["attributes"][field] = value
    with pytest.raises(ValueError, match=field):
        validate_team(team, rules)


def test_count_is_server_owned_and_spawn_slot_is_rejected():
    team, rules = settings()
    rules.update(survivor_count=2, team_points=40)
    team["survivors"] = team["survivors"][:2]
    assert len(validate_team(team, rules)["survivors"]) == 2
    team["survivors"][0]["spawn_slot"] = 1
    with pytest.raises(ValueError):
        validate_team(team, rules)


@pytest.mark.parametrize("mutation", ["budget", "duplicate", "unknown", "priorities", "personality", "threshold"])
def test_bad_teams_rejected(mutation):
    team, rules = settings()
    actor = team["survivors"][0]
    if mutation == "budget":
        actor["attributes"]["strength"] += 1
    elif mutation == "duplicate":
        actor["id"] = team["survivors"][1]["id"]
    elif mutation == "unknown":
        actor["behavior"]["script"] = "anything"
    elif mutation == "priorities":
        actor["behavior"]["priorities"] = ["explore"]
    elif mutation == "personality":
        actor["personality"]["generosity"] = 100
    else:
        actor["behavior"]["thresholds"]["hunger"] = 0
    with pytest.raises(ValueError):
        validate_team(team, rules)


def test_environment_spawns_independent_of_survivor_choices():
    team, rules = settings()
    a = create_simulation(team, rules, 17)
    b = create_simulation(team, rules, 17)
    assert [agent.position for agent in a.agents] == [agent.position for agent in b.agents]
    for _ in range(100):
        a.random.random()
        a.predator_behavior_random.random()
    for agent in a.agents:
        agent.position = (1, 1)
    for _ in range(4):
        a._spawn_predator()
        b._spawn_predator()
    assert [(p.kind, p.position, p.hunger, p.thirst) for p in a.predators] == [
        (p.kind, p.position, p.hunger, p.thirst) for p in b.predators]


def test_score_limit_and_reproducible_runs():
    team, rules = settings()
    rules.update(max_ticks=10, seeds=[17])
    first = compete(team, rules)
    second = compete(team, rules)
    assert first == second
    assert first["mean_score"] == 100
    sim = create_simulation(team, rules, 17)
    sim.tick = 10
    sim.agents[0].alive = False
    sim.agents[0].death_tick = 3
    assert score(sim)["score"] == 58
    assert sim.game_over


def test_map_presets_change_resources_not_terrain():
    team, rules = settings()
    easy = create_simulation(team, {**rules, "map_name": "easy-island"}, 17)
    hard = create_simulation(team, {**rules, "map_name": "hard-island"}, 17)
    assert [[t.terrain for t in row] for row in easy.world.tiles] == [[t.terrain for t in row] for row in hard.world.tiles]
    assert sum(sum(t.resources.values()) for row in easy.world.tiles for t in row) > sum(sum(t.resources.values()) for row in hard.world.tiles for t in row)
    assert easy.predator_interval > hard.predator_interval


def test_competition_rejects_model_and_duplicate_seeds():
    _, rules = settings()
    for patch in ({"decision_mode": "model"}, {"seeds": [1, 1]}, {"survivor_count": True}):
        with pytest.raises(ValueError):
            validate_competition({**deepcopy(rules), **patch})


def test_policies_and_attributes_are_applied():
    from bbagents import PlanStep
    team, rules = settings()
    sim = create_simulation(team, rules, 17)
    actor = sim.agents[0]
    assert (actor.intelligence, actor.endurance, actor.strength, actor.visibility) == (90, 3, 2, 4)
    assert actor.social_traits.generosity == 70
    actor.hunger, actor.thirst, actor.energy = 60, 0, 100
    assert sim.policy._emergency(actor) == "hunger emergency"
    actor.behavior["preferences"]["knowledge"] = "private"
    actor.behavior["trading"]["enabled"] = False
    with pytest.raises(ValueError, match="disabled"):
        sim.social.do_trade(actor, sim.agents[1].id, {"wood": 1}, {"stone": 1})
    actor.inventory["berries"] = 1
    sim.eat(actor, "berries")
    assert actor.hunger == 50
    actor.inventory["fish"] = 1
    sim.eat(actor, "fish")
    assert actor.hunger == 0
    actor.energy = 50
    actor.hunger = actor.thirst = 0
    actor.behavior["priorities"] = ["recover"] + [goal for goal in actor.behavior["priorities"] if goal != "recover"]
    sim.policy._update_objective(actor)
    assert actor.objective.startswith("Recover")
    assert sim.policy._call_for_step(actor, PlanStep("rest", "Rest"))[0].tool_name == "rest"


def test_duplicate_keys_are_rejected(tmp_path):
    for json5, suffix in ((True, "json5"), (False, "json")):
        path = tmp_path / f"bad.{suffix}"
        path.write_text('{"name": 1, "name": 2}')
        with pytest.raises(ValueError):
            load(path, json5=json5)


def test_low_food_threshold_actually_eats_before_default_target():
    from bbagents import PlanStep
    team, rules = settings()
    sim = create_simulation(team, rules, 17)
    actor = sim.agents[0]
    actor.behavior["thresholds"]["hunger"] = 1
    actor.hunger = 15
    actor.inventory["berries"] = 2
    call, done = sim.policy._call_for_step(actor, PlanStep("eat", "Eat"))
    assert call.tool_name == "eat" and not done


def test_weather_schedule_unaffected_by_decision_randomness():
    team, rules = settings()
    a = create_simulation(team, rules, 17)
    b = create_simulation(team, rules, 17)
    for _ in range(100):
        a.random.random()
    assert [a.weather_random.random() for _ in range(100)] == [b.weather_random.random() for _ in range(100)]

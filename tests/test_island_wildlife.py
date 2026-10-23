from collections import Counter
from types import SimpleNamespace

import pytest

from bbagents import PlanStep, ToolCall
from examples.survival_island import Simulation
from examples.survival_island.simulation import Terrain
from examples.survival_island.wildlife import Animal, SPECIES


def scene(kind="rabbit"):
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    actor = sim.agents[0]
    actor.position = (10, 10)
    actor.visibility = 4
    actor.known_resources.clear()
    for x in range(6, 15):
        for y in range(6, 15):
            sim.world.tile((x, y)).terrain = Terrain.GRASS
            sim.world.tile((x, y)).resources.clear()
    animal = Animal("prey", kind, (12, 10), SPECIES[kind]["health"])
    sim.wildlife.animals = [animal]
    sim.wildlife.hunting = SimpleNamespace(random=lambda: 0)
    return sim, actor, animal


def equip(sim, actor):
    sim.learn_skill(actor, "bow", "teacher")
    actor.inventory.update(wood=5, stone=1)
    sim.craft(actor, "bow")


def test_initial_animals_seeded_and_independent_of_survivor_randomness():
    first, second = Simulation(seed=17), Simulation(seed=17)
    assert Counter(animal.kind for animal in first.wildlife.animals) == {"deer": 5, "boar": 4, "rabbit": 8}
    assert [(a.kind, a.position) for a in first.wildlife.animals] == [(a.kind, a.position) for a in second.wildlife.animals]
    assert all(not first.world.tile(a.position).terrain.is_water for a in first.wildlife.animals)
    for _ in range(100):
        first.random.random()
    first.wildlife.update()
    second.wildlife.update()
    assert [a.position for a in first.wildlife.animals] == [a.position for a in second.wildlife.animals]


def test_bow_discovery_requires_age_intelligence_resources_and_seen_wildlife():
    sim, actor, _ = scene()
    actor.intelligence = 100
    actor.seen_wildlife = False
    actor.known_resources = {"wood": {(1, 1)}, "stone": {(2, 1)}}
    sim.tick = 200
    sim.discover_skills(actor)
    assert "bow" not in actor.learned_skills
    sim.observe(actor)
    sim.discover_skills(actor)
    assert "bow" in actor.learned_skills
    actor.inventory.update(wood=5, stone=1)
    sim.craft(actor, "bow")
    assert "hunting" in actor.learned_skills
    assert actor.inventory["bow"] == 1 and actor.inventory["wood"] == 0


def test_missing_tools_and_skills_reject_without_spending_inventory():
    sim, actor, animal = scene()
    actor.inventory.update(wood=5, stone=1)
    with pytest.raises(ValueError, match="skill"):
        sim.craft(actor, "bow")
    with pytest.raises(ValueError, match="hunt requires"):
        sim.wildlife.hunt(actor, animal.id)
    assert actor.inventory["wood"] == 5
    actor.inventory["bow"] = 1  # Receiving equipment does not teach hunting.
    assert not sim.wildlife.can_hunt(actor, animal)


@pytest.mark.parametrize("kind", ["deer", "boar", "rabbit"])
def test_hunting_yield_and_no_double_harvest(kind):
    sim, actor, animal = scene(kind)
    equip(sim, actor)
    animal.health = 1
    result = sim.tools[actor.id].execute(ToolCall("hunt", {"animal_id": animal.id}))
    assert result.metadata["meat"] == SPECIES[kind]["meat"]
    assert not animal.alive
    sim.tick += 4
    with pytest.raises(ValueError):
        sim.wildlife.hunt(actor, animal.id)
    assert actor.inventory["meat"] == SPECIES[kind]["meat"]
    actor.hunger = 70
    sim.eat(actor, "meat")
    assert actor.hunger == 35
    assert sim.trace.events_named("wildlife.killed")[-1].attributes["agent"] == actor.id


def test_missed_shot_costs_time_and_energy_and_cannot_hit_unseen_prey():
    sim, actor, animal = scene()
    equip(sim, actor)
    sim.wildlife.hunting = SimpleNamespace(random=lambda: 1)
    before = actor.energy
    assert not sim.wildlife.hunt(actor, animal.id).metadata["hit"]
    assert actor.energy == before - 4
    with pytest.raises(ValueError):
        sim.wildlife.hunt(actor, animal.id)
    sim.tick += 4
    animal.position = (20, 20)
    with pytest.raises(ValueError):
        sim.wildlife.hunt(actor, animal.id)


@pytest.mark.parametrize("kind", ["deer", "rabbit"])
def test_prey_flee_and_stay_on_land(kind):
    sim, actor, animal = scene(kind)
    distance = sim.distance(actor.position, animal.position)
    sim.wildlife.update()
    assert animal.behavior == "fleeing"
    assert sim.distance(actor.position, animal.position) > distance
    assert not sim.world.tile(animal.position).terrain.is_water


def test_wildlife_eats_finite_berries_and_feeding_changes_hunger():
    sim, actor, animal = scene("deer")
    actor.position = (1, 1)
    animal.hunger = 50
    tile = sim.world.tile(animal.position)
    tile.resources["berries"] = 1
    sim.wildlife.update()
    assert tile.resources["berries"] == 0
    assert animal.hunger < 30
    assert sim.trace.events_named("wildlife.fed")


def test_boar_retaliates_on_cooldown_and_death_is_attributed():
    sim, actor, animal = scene("boar")
    equip(sim, actor)
    animal.position = (11, 10)
    sim.wildlife.hunt(actor, animal.id)
    actor.health = 15
    sim.wildlife.update()
    assert actor.health == 5
    sim.wildlife.update()
    assert actor.health == 5
    sim.tick = 5
    sim.wildlife.update()
    sim._record_states()
    assert not actor.alive and actor.death_cause == "boar attack (prey)"
    assert actor.death_tick == 5


def test_reference_food_controller_crafts_hunts_and_eats():
    sim, actor, animal = scene()
    sim.learn_skill(actor, "bow", "teacher")
    actor.inventory.update(wood=5, stone=1)
    actor.hunger = 70
    animal.health = 1
    for expected in ("craft", "hunt", "eat"):
        call, _ = sim.policy._call_for_step(actor, PlanStep("eat", "Eat"))
        assert call.tool_name == expected
        sim.tools[actor.id].execute(call)
    assert actor.hunger == 35


def test_model_tasks_craft_bow_and_hunt_through_same_tools():
    from examples.survival_island.model_policy import ModelPolicy
    sim, actor, animal = scene()
    sim.learn_skill(actor, "bow", "teacher")
    actor.inventory.update(wood=5, stone=1)
    animal.health = 1
    policy = ModelPolicy(sim, model="fake")
    sim.policy = policy
    try:
        call, done = policy._task_call(actor, PlanStep("bow", "Make bow", description="craft_bow"))
        assert call == ToolCall("craft", {"item": "bow"}) and not done
        sim.tools[actor.id].execute(call)
        call, done = policy._task_call(actor, PlanStep("hunt", "Hunt", description="hunt_food"))
        assert call == ToolCall("hunt", {"animal_id": animal.id}) and not done
        sim.tools[actor.id].execute(call)
        assert policy._task_call(actor, PlanStep("hunt", "Hunt", description="hunt_food"))[1]
    finally:
        policy.close()


def test_local_teacher_can_teach_bow_and_hunting_early():
    sim = Simulation(agents=2, predator_interval=None)
    teacher, student = sim.agents
    teacher.position = student.position = (10, 10)
    sim.observe(teacher)
    sim.observe(student)
    equip(sim, teacher)
    for topic in ("bow", "hunting"):
        sim.social.execute(teacher, "communicate", agent_id=student.id,
                           message={"kind": "information", "topic": topic})
        sim.policy._read_messages(student)
    assert {"bow", "hunting"} <= student.learned_skills
    assert sim.tick == 0

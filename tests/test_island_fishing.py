import pytest

from examples.survival_island import Simulation
from examples.survival_island.simulation import Terrain


def setup():
    sim = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    actor, other = sim.agents
    actor.position = other.position = (1, 1)
    sim.world.tile((2, 1)).terrain = Terrain.LAKE
    sim.world.tile((2, 1)).fish_stock = 3
    sim.observe(actor)
    sim.observe(other)
    return sim, actor, other


def test_intelligence_and_personal_age_control_discovery():
    sim, smart, slow = setup()
    smart.intelligence, slow.intelligence = 100, 0
    sim.tick = 200
    sim.discover_skills(smart)
    sim.discover_skills(slow)
    assert "fishing" in smart.learned_skills
    assert "fishing" not in slow.learned_skills
    slow.born_tick = 200
    sim.tick = 700
    sim.discover_skills(slow)
    assert "fishing" not in slow.learned_skills
    sim.tick = 900
    sim.discover_skills(slow)
    assert "fishing" in slow.learned_skills


def test_fishing_requires_knowledge_rod_lake_energy_and_cooldown():
    sim, actor, _ = setup()
    actor.inventory["wood"] = 4
    with pytest.raises(ValueError, match="skill"):
        sim.craft(actor, "fishing_rod")
    sim.learn_skill(actor, "fishing", "discovery")
    sim.craft(actor, "fishing_rod")
    sim.fish(actor)
    assert actor.inventory["fish"] == 1
    with pytest.raises(ValueError):
        sim.fish(actor)
    actor.hunger = 70
    sim.eat(actor, "fish")
    assert actor.hunger == 20
    sim.tick += 8
    sim.world.tile((2, 1)).terrain = Terrain.RIVER
    sim.world.tile((1, 1)).terrain = Terrain.GRASS
    for pos in ((0, 1), (1, 0), (1, 2)):
        sim.world.tile(pos).terrain = Terrain.GRASS
    assert not sim.can_fish(actor)


def test_teacher_must_know_skill_and_local_message_teaches_immediately():
    sim, teacher, student = setup()
    with pytest.raises(ValueError):
        sim.social.information(teacher, "fishing")
    sim.learn_skill(teacher, "fishing", "discovery")
    sim.social.execute(teacher, "communicate", agent_id=student.id,
                       message={"kind": "information", "topic": "fishing"})
    sim.policy._read_messages(student)
    assert student.learned_skills == {"fishing"}
    assert sim.tick == 0


def test_local_food_policy_crafts_fishes_and_eats():
    from bbagents import PlanStep
    sim, actor, _ = setup()
    sim.learn_skill(actor, "fishing", "discovery")
    actor.inventory["wood"] = 4
    actor.hunger = 75
    for expected in ("craft", "fish", "eat"):
        call, _ = sim.policy._call_for_step(actor, PlanStep("eat", "Eat"))
        assert call.tool_name == expected
        sim.tools[actor.id].execute(call)
    assert actor.hunger == 25

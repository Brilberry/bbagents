import pytest

from bbagents import Plan, PlanStep, ToolCall
from examples.survival_island import Simulation
from examples.survival_island.model_policy import ModelPolicy


def test_tools_require_skill_and_smarter_survivors_discover_earlier():
    sim = Simulation(agents=2, predator_interval=None)
    smart, slow = sim.agents
    for agent, intelligence in ((smart, 100), (slow, 10)):
        agent.intelligence = intelligence
        agent.known_resources = {"wood": {(1, 1)}, "stone": {(2, 1)}}
        agent.inventory.update(wood=10, stone=2)
        with pytest.raises(ValueError, match="skill"):
            sim.craft(agent, "axe")
    sim.tick = 150
    sim.discover_skills(smart)
    sim.discover_skills(slow)
    assert {"axe", "spear"} <= smart.learned_skills
    assert not {"axe", "spear"} & slow.learned_skills
    sim.craft(smart, "axe")
    assert ToolCall("craft", {"item": "axe"}) not in sim.available_actions(slow)


def test_teaching_tools_uses_local_information_flow():
    sim = Simulation(agents=2, predator_interval=None)
    teacher, student = sim.agents
    teacher.position = student.position = (1, 1)
    sim.observe(teacher)
    sim.observe(student)
    sim.learn_skill(teacher, "axe", "discovery")
    sim.social.execute(teacher, "communicate", agent_id=student.id,
                       message={"kind": "information", "topic": "axe"})
    sim.policy._read_messages(student)
    assert "axe" in student.learned_skills


def test_tool_utility_not_universal_progression():
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    agent.learned_skills.update({"axe", "spear"})
    agent.home = (1, 1)
    assert sim.policy.tool_objective(agent) is None
    agent.last_threat_tick = sim.tick
    assert sim.policy.tool_objective(agent) == "Prepare personal defenses"
    agent.inventory["spear"] = 1
    agent.home = None
    agent.wood_gathered = 10
    assert sim.policy.tool_objective(agent).startswith("Develop")
    agent.inventory["wood"] = 10
    assert sim.policy.tool_objective(agent) is None


def test_model_axe_plan_and_active_goal_match():
    sim = Simulation(agents=1, predator_interval=None)
    agent = sim.agents[0]
    policy = ModelPolicy(sim, model="fake")
    sim.policy = policy
    try:
        step = PlanStep("axe", "Make an axe", description="craft_axe")
        call, _ = policy._task_call(agent, step)
        assert call.tool_name == "observe_surroundings"
        assert step.error == "Must learn axe before crafting it"
        sim.learn_skill(agent, "axe", "discovery")
        agent.inventory.update(wood=3, stone=1)
        fresh = PlanStep("axe", "Make an axe", description="craft_axe")
        assert policy._task_call(agent, fresh)[0] == ToolCall("craft", {"item": "axe"})
        policy.model_plans[agent.id] = Plan("Make an axe for rebuilding", [fresh])
        agent.decision_source = "model plan"
        assert sim.active_objective(agent) == "Make an axe for rebuilding"
        agent.decision_source = "rule"
        assert sim.active_objective(agent) == agent.current_plan.goal
    finally:
        policy.close()

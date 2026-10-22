from examples.survival_island import Simulation
from examples.survival_island.simulation import Terrain


def test_island_has_expected_terrain_and_agents() -> None:
    simulation = Simulation(seed=11)
    terrain = {tile.terrain for row in simulation.world.tiles for tile in row}

    assert len(simulation.agents) == 5
    assert {Terrain.GRASS, Terrain.FOREST, Terrain.RIVER, Terrain.MOUNTAIN} <= terrain
    assert all(len(agent.known_tiles) > 0 for agent in simulation.agents)


def test_agents_use_registered_bounded_tools() -> None:
    simulation = Simulation(seed=9)

    for _ in range(20):
        simulation.step()

    started = simulation.trace.events_named("tool.started")
    assert started
    allowed = {name for name, _ in simulation.tools["agent-1"].descriptions()}
    assert all(event.attributes["tool_name"] in allowed for event in started)


def test_survival_pressure_interrupts_existing_plan() -> None:
    simulation = Simulation(seed=5)
    agent = simulation.agents[0]
    original_version = agent.current_plan.version
    agent.thirst = 70

    simulation.step()

    assert agent.current_plan.version > original_version
    assert agent.current_plan.goal == "Resolve dangerous thirst"
    assert agent.plan_reason == "thirst emergency"


def test_agents_make_progress_over_long_run() -> None:
    simulation = Simulation(seed=7)

    for _ in range(260):
        simulation.step()

    gathered = sum(sum(agent.inventory.values()) for agent in simulation.agents)
    shelters = sum(tile.shelter_owner is not None for row in simulation.world.tiles for tile in row)
    assert gathered > 0
    assert shelters > 0
    assert any(message.text.startswith("river:") for message in simulation.messages)


def test_survivors_live_beyond_original_failure_window() -> None:
    simulation = Simulation(seed=7, predator_interval=None)
    for _ in range(1000):
        simulation.step()
    assert all(agent.alive and agent.health == 100 for agent in simulation.agents)


def test_model_proposal_is_executed_through_tools() -> None:
    from examples.survival_island.model_policy import ModelPolicy

    simulation = Simulation()
    agent = simulation.agents[0]
    policy = ModelPolicy(simulation, model="fake", requester=lambda *args: {
        "goal": "Survey terrain", "plan": ["Look around"],
        "tool": "observe_surroundings", "arguments": {},
    })
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        call = policy.decide(agent)
        observation = simulation.tools[agent.id].execute(call)
        assert observation.source == "world"
        assert policy.model_plans[agent.id].goal == "Survey terrain"
    finally:
        policy.close()


def test_predators_arrive_on_schedule():
    simulation = Simulation()
    for _ in range(249):
        simulation.step()
    assert not simulation.predators
    simulation.step()
    assert len(simulation.predators) == 1
    for _ in range(500):
        simulation.step()
    assert len(simulation.predators) == 3
    assert [item.kind for item in simulation.predators] == ["wolf", "wolf", "bear"]


def test_survivor_can_craft_spear_and_defeat_predator():
    from examples.survival_island.simulation import Predator
    from bbagents import ToolCall

    simulation = Simulation()
    agent = simulation.agents[0]
    agent.inventory["wood"] = 3
    simulation.tools[agent.id].execute(ToolCall("craft", {"item": "spear"}))
    wolf = Predator("wolf-test", "wolf", agent.position, 45)
    simulation.predators.append(wolf)
    assert simulation.policy.decide(agent).tool_name == "attack"
    simulation.tools[agent.id].execute(ToolCall("attack", {"predator_id": wolf.id}))
    assert wolf.health == 20
    simulation.tools[agent.id].execute(ToolCall("attack", {"predator_id": wolf.id}))
    assert not wolf.alive
    assert not simulation.visible_predators(agent)


def test_flee_rest_and_shelter_defense():
    import pytest
    from examples.survival_island.simulation import Predator

    simulation = Simulation()
    agent = simulation.agents[0]
    wolf = Predator("wolf-test", "wolf", agent.position, 45)
    simulation.predators.append(wolf)
    with pytest.raises(ValueError):
        simulation.rest(agent)
    before = simulation.distance(agent.position, wolf.position)
    simulation.flee(agent)
    assert simulation.distance(agent.position, wolf.position) > before
    wolf.position = agent.position
    simulation.world.tile(agent.position).shelter_owner = agent.id
    # Remove other survivors so the attack target is unambiguous.
    for other in simulation.agents[1:]:
        other.alive = False
    simulation.tick = 4
    simulation._update_predators()
    assert agent.health == 98  # shelter reduces the wolf's 8 damage to 2
    wolf.health = 0
    simulation.rest(agent)
    assert agent.health > 98


def test_observation_never_restores_energy():
    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    agent.energy = 50
    agent.last_action = "Observed terrain"
    simulation.world.tile(agent.position).shelter_owner = agent.id
    simulation.observe(agent)
    assert agent.energy == 50
    simulation._apply_needs(agent)
    assert agent.energy < 50


def test_explicit_rest_recovers_faster_in_shelter():
    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    agent.energy = 40
    simulation.rest(agent)
    assert agent.energy == 43
    simulation.world.tile(agent.position).shelter_owner = agent.id
    simulation.rest(agent)
    assert agent.energy == 49


def test_reference_policy_recovers_then_resumes_work():
    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    agent.energy = 29
    assert simulation.policy.decide(agent).tool_name == "rest"
    assert agent.plan_reason == "energy emergency"
    for _ in range(15):
        call = simulation.policy.decide(agent)
        simulation.tools[agent.id].execute(call)
    assert agent.energy >= 70
    simulation.policy.decide(agent)
    assert agent.plan_reason == "resume personal objective"


def test_active_agent_energy_cycles_instead_of_staying_full():
    simulation = Simulation(predator_interval=None)
    explorer = simulation.agents[3]
    energies = []
    rest_count = 0
    for _ in range(300):
        simulation.step()
        energies.append(explorer.energy)
        rest_count += explorer.last_action == "rested"
    assert min(energies) < 35
    assert rest_count > 0
    assert sum(value > 90 for value in energies) < 40


def test_sleep_advances_across_ticks_and_restores_saved_plan():
    from bbagents import Plan, PlanStep, StepStatus

    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    agent.current_plan = Plan("Explore the northern coast", [
        PlanStep("done", "Previous work", status=StepStatus.DONE, evidence=["found river"]),
        PlanStep("explore", "Continue exploration", depends_on=["done"])], version=4)
    agent.plan_reason = "personal goal"
    agent.energy = 30
    simulation.sleep(agent)
    key = agent.suspended_plan_key
    initial_version = simulation.checkpoints.load(key).version
    position = agent.position
    for _ in range(11):
        simulation.step()
        assert agent.position == position
    assert agent.sleep_remaining == 1
    assert simulation.checkpoints.load(key).version > initial_version
    simulation.step()
    assert agent.sleep_remaining == 0
    assert agent.current_plan.goal == "Explore the northern coast"
    assert agent.current_plan.version == 4
    assert agent.current_plan.steps[0].evidence == ["found river"]
    agent.current_plan.steps[1].evidence.append("new evidence after resuming")
    assert simulation.checkpoints.load(key).value["plan"]["steps"][1]["evidence"] == []
    assert simulation.checkpoints.load(key).value["status"] == "resumed"
    assert simulation.trace.events_named("sleep.completed")
    assert simulation.trace.events_named("plan.resumed")
    assert agent.memory.retrieve("sleep")[0].record.metadata["status"] == "completed"


def test_predator_interrupts_sleep_and_resume_waits_until_safe():
    from examples.survival_island.simulation import Predator

    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    goal = agent.current_plan.goal
    agent.inventory["spear"] = 1
    simulation.sleep(agent)
    wolf = Predator("interrupting-wolf", "wolf", agent.position, 45)
    simulation.predators.append(wolf)
    simulation.step()
    assert agent.sleep_remaining == 0
    assert agent.resume_pending
    assert wolf.health == 20  # immediate defense on the wake tick
    assert simulation.trace.events_named("sleep.interrupted")
    episode = agent.memory.retrieve("sleep")[0].record
    assert episode.metadata["reason"] == "predator"
    wolf.health = 0
    simulation.step()
    assert not agent.resume_pending
    assert agent.current_plan.goal == goal
    assert simulation.trace.events_named("plan.resumed")


def test_sleep_interrupts_for_thirst_and_shelter_improves_recovery():
    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    agent.energy = 20
    simulation.sleep(agent)
    simulation._advance_sleep(agent)
    outdoor = agent.energy - 20
    simulation.world.tile(agent.position).shelter_owner = agent.id
    before = agent.energy
    simulation._advance_sleep(agent)
    assert agent.energy - before > outdoor
    agent.thirst = 80
    simulation._advance_sleep(agent)
    assert not agent.sleep_remaining
    assert agent.resume_pending
    assert agent.memory.retrieve("sleep")[0].record.metadata["reason"] == "thirst"


def test_night_preparation_has_dependencies_and_uses_sleep_memory():
    from bbagents.memory import MemoryRecord

    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    original_goal = agent.current_plan.goal
    simulation.tick = 44
    site = agent.position
    simulation.world.tile(site).shelter_owner = agent.id
    agent.memory.remember(MemoryRecord("sleep interrupted by predator", metadata={
        "position": site, "reason": "predator", "status": "interrupted"}))
    simulation.policy.prepare_night(agent)
    assert agent.current_plan.goal == "Prepare for night"
    assert simulation.checkpoints.load(agent.suspended_plan_key).value["plan"]["goal"] == original_goal
    assert agent.current_plan.steps[-1].depends_on == ["wait-night"]
    step = agent.current_plan.steps[2]
    _, finished = simulation.policy._call_for_step(agent, step)
    assert finished  # unsafe remembered shelter is excluded; sleep outdoors instead


def test_model_cannot_call_unknown_tool_or_supply_extra_arguments() -> None:
    import pytest
    from examples.survival_island.model_policy import validate_decision

    for tool, arguments in [("execute_python", {}), ("drink", {"agent": "agent-2"})]:
        with pytest.raises(ValueError):
            validate_decision({"goal": "cheat", "plan": ["cheat"],
                               "tool": tool, "arguments": arguments})


def test_framework_retries_invalid_model_response_and_traces_attempts() -> None:
    from examples.survival_island.model_policy import ModelPolicy

    simulation = Simulation()
    calls = []

    def requester(*args):
        calls.append(args)
        return {"goal": "Survey", "plan": ["Look around"],
                "tool": "bad" if len(calls) == 1 else "observe_surroundings",
                "arguments": {}}

    policy = ModelPolicy(simulation, model="fake", requester=requester)
    try:
        agent = simulation.agents[0]
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        # A slow response is no longer rejected just because ticks advanced.
        simulation.tick = 100
        assert policy.decide(agent).tool_name == "observe_surroundings"
        assert policy.accepted == 1
        assert len(calls) == 2
        assert simulation.trace.events_named("decision.failed")
        assert simulation.trace.events_named("decision.completed")
    finally:
        policy.close()


def test_model_failure_exposes_reason_in_status() -> None:
    from examples.survival_island.model_policy import ModelPolicy

    def requester(*args):
        raise RuntimeError("Ollama HTTP 404: model missing")

    simulation = Simulation()
    policy = ModelPolicy(simulation, model="missing", requester=requester)
    try:
        agent = simulation.agents[0]
        policy.decide(agent)
        import pytest
        with pytest.raises(Exception):
            policy.pending[agent.id][1].result(timeout=2)
        policy.decide(agent)
        assert policy.failed == 1
        assert "404" in policy.status[agent.id]
        assert "model missing" in policy.status[agent.id]
    finally:
        policy.close()


def test_available_actions_and_depletion_memory():
    from bbagents import ToolCall

    simulation = Simulation()
    agent = simulation.agents[0]
    tile = simulation.world.tile(agent.position)
    tile.resources.clear()
    tile.resources["wood"] = 1
    simulation.observe(agent)
    assert agent.position in agent.known_resources["wood"]
    assert simulation.action_is_available(ToolCall("gather", {"resource": "wood"}),
                                          simulation.available_actions(agent))
    simulation.gather(agent, "wood")
    assert agent.position not in agent.known_resources["wood"]
    assert not simulation.action_is_available(ToolCall("gather", {"resource": "wood"}),
                                              simulation.available_actions(agent))
    agent.known_resources.setdefault("berries", set()).add(agent.position)
    simulation.observe(agent)
    assert agent.position not in agent.known_resources["berries"]


def test_unavailable_model_action_fails_framework_validation_and_is_fed_back():
    import pytest
    from examples.survival_island.model_policy import ModelPolicy

    simulation = Simulation()
    agent = simulation.agents[0]
    simulation.world.tile(agent.position).resources.clear()
    snapshots = []

    def requester(endpoint, model, snapshot):
        snapshots.append(snapshot)
        return {"goal": "Gather", "plan": ["Gather wood"], "tool": "gather",
                "arguments": {"resource": "wood"}}

    policy = ModelPolicy(simulation, model="fake", requester=requester)
    try:
        policy.decide(agent)
        with pytest.raises(Exception):
            policy.pending[agent.id][1].result(timeout=2)
        policy.decide(agent)
        assert policy.accepted == 0
        assert policy.failed == 1
        assert agent.inventory["wood"] == 0
        assert simulation.trace.events_named("decision.failed")
        from bbagents import Observation
        for _ in range(12):
            agent.feedback.append(Observation("world", "Observed idle terrain"))
        simulation.tick += policy.interval
        policy.decide(agent)
        with pytest.raises(Exception):
            policy.pending[agent.id][1].result(timeout=2)
        assert snapshots[-1]["last_failure"]["source"] == "decision.failure"
        assert "gather" in snapshots[-1]["last_failure"]["content"]
        assert "current_tile" in snapshots[-1]
        assert "available_actions" in snapshots[-1]
    finally:
        policy.close()


def test_model_action_rechecked_if_another_agent_depletes_resource():
    from examples.survival_island.model_policy import ModelPolicy

    simulation = Simulation()
    agent = simulation.agents[0]
    tile = simulation.world.tile(agent.position)
    tile.resources["wood"] = 1
    policy = ModelPolicy(simulation, model="fake", requester=lambda *args: {
        "goal": "Gather", "plan": ["Gather wood"], "tool": "gather",
        "arguments": {"resource": "wood"}})
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        tile.resources["wood"] = 0
        call = policy.decide(agent)
        assert call.tool_name != "gather" or call.arguments.get("resource") != "wood"
        assert policy.accepted == 0
        assert simulation.trace.events_named("action.rejected")
        assert any(item.source == "action.rejected" for item in agent.feedback)
    finally:
        policy.close()

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
    simulation = Simulation(seed=7, lightning_probability=0)

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
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=lambda *args: {
        "goal": "Survey terrain", "milestones": ["explore"]})
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        call = policy.decide(agent)
        result = simulation.tools[agent.id].execute(call)
        policy.on_result(agent, call, result=result)
        assert call.tool_name == "move"
        assert policy.model_plans[agent.id].goal == "Survey terrain"
        assert policy.model_plans[agent.id].steps[0].evidence
    finally:
        policy.close()

def test_predators_arrive_on_schedule():
    simulation = Simulation()
    for _ in range(249):
        simulation.step()
    assert not simulation.predators
    simulation.step()
    first_pack_size = len(simulation.predators)
    assert first_pack_size in (2, 3)
    assert len({item.pack_id for item in simulation.predators}) == 1
    for _ in range(500):
        simulation.step()
    assert len(simulation.predators) - first_pack_size - 1 in (2, 3)
    assert all(item.kind == "wolf" for item in simulation.predators[:-1])
    assert simulation.predators[-1].kind == "bear"
    assert simulation.predators[-1].pack_id is None


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
    simulation.tick += 3
    simulation.tools[agent.id].execute(ToolCall("attack", {"predator_id": wolf.id}))
    assert not wolf.alive
    assert not simulation.visible_predators(agent)


def test_random_pack_sizes_and_contact_merges():
    from examples.survival_island.simulation import Predator

    sizes = set()
    for seed in range(12):
        simulation = Simulation(seed=seed)
        simulation._spawn_predator()
        sizes.add(len(simulation.predators))
    assert sizes == {2, 3}
    simulation.predators = [
        Predator("w1", "wolf", (3, 3), 45, "a"),
        Predator("w2", "wolf", (4, 3), 45, "b"),
        Predator("w3", "wolf", (5, 3), 45, "c"),
        Predator("far", "wolf", (25, 25), 45, "far-pack"),
        Predator("dead", "wolf", (3, 3), 0, "dead-pack"),
        Predator("bear", "bear", (3, 3), 150),
    ]
    simulation._merge_wolf_packs()
    assert len({item.pack_id for item in simulation.predators[:3]}) == 1
    assert simulation.predators[3].pack_id == "far-pack"
    assert simulation.predators[4].pack_id == "dead-pack"
    assert simulation.predators[5].pack_id is None
    assert simulation.trace.events_named("predator.packs_merged")


def test_attack_cooldown_and_solo_bear_wolf_encounter():
    import pytest
    from examples.survival_island.simulation import Predator

    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.inventory["spear"] = 1
    bear = Predator("bear", "bear", agent.position, 150)
    wolf = Predator("wolf", "wolf", agent.position, 45, "pack")
    simulation.predators = [bear, wolf]
    assert simulation.policy.decide(agent).tool_name == "flee"
    simulation.attack(agent, bear.id)
    health = bear.health
    with pytest.raises(ValueError, match="cooldown"):
        simulation.attack(agent, bear.id)
    assert bear.health == health
    for _ in range(20):
        simulation.tick += 1
        if not agent.alive:
            break
        if simulation.tick >= agent.next_attack_tick and agent.energy >= 4:
            simulation.attack(agent, bear.id)
        simulation._update_predators()
    assert not agent.alive
    assert bear.alive


def test_new_survivors_arrive_at_1000_tick_intervals_with_tools():
    simulation = Simulation(predator_interval=None, lightning_probability=0)
    simulation.tick = 998
    simulation.step()
    assert len(simulation.agents) == 5
    simulation.step()
    newcomer = simulation.agents[-1]
    assert len(simulation.agents) == 6
    assert newcomer.id in simulation.tools
    assert newcomer.current_plan is not None
    assert len(newcomer.known_tiles) < simulation.world.size ** 2
    spawn = simulation.trace.events_named("survivor.arrived")[-1].attributes["position"]
    assert 0 in spawn or simulation.world.size - 1 in spawn
    simulation.tick = 1999
    simulation.step()
    assert len(simulation.agents) == 7
    assert len({agent.id for agent in simulation.agents}) == 7


def test_claim_abandoned_shelter_and_reuse_owned_home():
    import pytest

    simulation = Simulation(predator_interval=None, lightning_probability=0)
    owner, claimant = simulation.agents[:2]
    claimant.objective = "Build a safe shelter"
    position = claimant.position
    simulation.world.tile(position).shelter_owner = owner.id
    owner.home = position
    with pytest.raises(ValueError, match="unoccupied"):
        simulation.claim_shelter(claimant)
    owner.alive = False
    simulation.observe(claimant)
    simulation.policy.replan(claimant, "find home")
    assert simulation.policy.decide(claimant).tool_name == "claim_shelter"
    simulation.claim_shelter(claimant)
    simulation.policy.replan(claimant, "resume personal objective")
    assert claimant.current_plan.goal == "Maintain existing shelter"
    assert claimant.home == position
    assert simulation.world.tile(position).shelter_owner == claimant.id


def test_lightning_destroys_shelter_and_interrupts_sleep():
    simulation = Simulation(predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.home = agent.position
    simulation.world.tile(agent.position).shelter_owner = agent.id
    simulation.sleep(agent)
    result = simulation.lightning_strike(agent.position)
    assert result.metadata["destroyed_shelter"]
    assert simulation.world.tile(agent.position).shelter_owner is None
    assert agent.home is None
    assert not agent.sleep_remaining
    assert agent.resume_pending
    assert simulation.trace.events_named("calamity.lightning")
    assert agent.memory.retrieve("sleep")[0].record.metadata["reason"] == "lightning"


def test_random_lightning_is_seeded_and_can_be_disabled():
    results = []
    for _ in range(2):
        simulation = Simulation(seed=8, predator_interval=None, lightning_probability=0.05)
        for _ in range(100):
            simulation.step()
        events = simulation.trace.events_named("calamity.lightning")
        results.append([(item.attributes["tick"], item.attributes["position"]) for item in events])
    assert results[0] == results[1]
    assert 0 < len(results[0]) < 100


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
    assert agent.health == 98.25  # shelter reduces the wolf's 7 damage to 1.75
    wolf.health = 0
    simulation.rest(agent)
    assert agent.health > 98.25


def test_wolf_pack_shares_target_while_bear_hunts_independently():
    from examples.survival_island.simulation import Predator

    simulation = Simulation(predator_interval=None)
    for row in simulation.world.tiles:
        for tile in row:
            tile.terrain = Terrain.GRASS
            tile.shelter_owner = None
    first, second = simulation.agents[:2]
    first.position = (10, 10)
    second.position = (14, 10)
    for agent in simulation.agents[2:]:
        agent.alive = False
    simulation.predators = [
        Predator("w1", "wolf", (10, 11), 45, "pack-test"),
        Predator("w2", "wolf", (13, 10), 45, "pack-test"),
        Predator("w3", "wolf", (12, 11), 45, "pack-test"),
        Predator("bear", "bear", (14, 11), 75),
    ]
    simulation.tick = 4
    simulation._update_predators()
    assert first.health == 93
    assert second.health == 70  # only the independent bear attacks this survivor
    assert simulation.predators[1].position == (12, 10)  # joins pack's prey, despite nearby second agent


def test_three_adjacent_wolves_deal_less_combined_damage_than_one_bear():
    from examples.survival_island.simulation import Predator

    simulation = Simulation(predator_interval=None)
    agent = simulation.agents[0]
    for other in simulation.agents[1:]:
        other.alive = False
    simulation.world.tile(agent.position).shelter_owner = None
    simulation.predators = [Predator(f"w{i}", "wolf", agent.position, 45, "pack") for i in range(3)]
    simulation.tick = 4
    simulation._update_predators()
    assert agent.health == 79
    simulation.predators = [Predator("bear", "bear", agent.position, 75)]
    simulation._update_predators()
    assert agent.health == 49


def test_bear_attack_cooldown_persists_when_target_changes():
    from examples.survival_island.simulation import Predator

    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    first, second = simulation.agents
    first.position = second.position = (10, 10)
    simulation.world.tile(first.position).shelter_owner = None
    bear = Predator("bear", "bear", first.position, 150)
    simulation.predators = [bear]
    simulation.tick = 2
    simulation._update_predators()
    assert first.health == 70
    first.position = (25, 25)
    for tick in (2, 3, 4, 5, 6, 7):
        simulation.tick = tick
        simulation._update_predators()
        assert second.health == 100
    simulation.tick = 8
    simulation._update_predators()
    assert second.health == 70


def test_wolf_attacks_every_four_ticks_with_increased_damage():
    from examples.survival_island.simulation import Predator

    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    simulation.world.tile(agent.position).shelter_owner = None
    simulation.predators = [Predator("wolf", "wolf", agent.position, 45)]
    for tick in range(1, 9):
        simulation.tick = tick
        simulation._update_predators()
        assert agent.health == 100 - 7 * (tick // 4)


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


def test_invalid_model_response_waits_for_next_planning_window() -> None:
    import pytest
    from bbagents.decision import DecisionFailure
    from examples.survival_island.model_policy import ModelPolicy
    simulation = Simulation(agents=1)
    calls = []
    def requester(*args):
        calls.append(args)
        return {"goal": "Survey", "milestones": ["bad" if len(calls) == 1 else "explore"]}
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=requester)
    try:
        agent = simulation.agents[0]
        policy.decide(agent)
        with pytest.raises(DecisionFailure):
            policy.pending[agent.id][1].result(timeout=2)
        simulation.tick = 100
        assert policy.decide(agent).tool_name == "move"
        assert policy.accepted == 0
        assert len(calls) == 1
        assert not policy.pending
        assert simulation.trace.events_named("decision.failed")
    finally:
        policy.close()

def test_model_failure_exposes_reason_in_status() -> None:
    import pytest
    from bbagents.decision import DecisionFailure
    from examples.survival_island.model_policy import ModelPolicy
    def requester(*args):
        raise RuntimeError("Ollama HTTP 404: model missing")
    simulation = Simulation(agents=1)
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="missing", cooldown=0, requester=requester)
    try:
        agent = simulation.agents[0]
        policy.decide(agent)
        with pytest.raises(DecisionFailure):
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
    from bbagents import Observation
    from bbagents.decision import DecisionFailure
    from examples.survival_island.model_policy import ModelPolicy
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    snapshots = []
    now = [0.0]
    def requester(endpoint, model, snapshot):
        snapshots.append(snapshot)
        return {"goal": "Gather", "tool": "gather", "arguments": {"resource": "wood"}}
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=requester, clock=lambda: now[0])
    try:
        policy.decide(agent)
        with pytest.raises(DecisionFailure):
            policy.pending[agent.id][1].result(timeout=2)
        policy.decide(agent)
        assert policy.accepted == 0
        assert policy.failed == 1
        assert simulation.trace.events_named("decision.failed")
        for _ in range(12):
            agent.feedback.append(Observation("world", "Observed idle terrain"))
        now[0] += policy.interval * 2 + 1
        policy.decide(agent)
        with pytest.raises(DecisionFailure):
            policy.pending[agent.id][1].result(timeout=2)
        assert snapshots[-1]["last_failure"]["source"] == "decision.failure"
        assert "task_catalog" in snapshots[-1]
    finally:
        policy.close()

def test_model_action_rechecked_if_another_agent_depletes_resource():
    from examples.survival_island.model_policy import ModelPolicy
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    tile = simulation.world.tile(agent.position)
    tile.resources["wood"] = 1
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=lambda *args: {
        "goal": "Gather", "milestones": ["gather_wood"]})
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        tile.resources["wood"] = 0
        call = policy.decide(agent)
        assert call.tool_name != "gather" or call.arguments.get("resource") != "wood"
        assert policy.accepted == 1
        assert simulation.action_is_available(call, simulation.available_actions(agent))
    finally:
        policy.close()

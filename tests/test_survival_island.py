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
    simulation = Simulation(seed=7)
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

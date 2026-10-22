from examples.survival_island import Simulation
from examples.survival_island.model_policy import ModelPolicy


def test_intelligence_is_seeded_diverse_and_given_to_new_arrivals():
    first, second = Simulation(seed=17), Simulation(seed=17)
    scores = [agent.intelligence for agent in first.agents]
    assert scores == [agent.intelligence for agent in second.agents]
    assert max(scores) >= 85
    assert min(scores) <= 20
    first._spawn_survivor()
    assert 5 <= first.agents[-1].intelligence <= 100
    assert first.trace.events_named("survivor.state")[0].attributes["intelligence"] == scores[0]


def test_initial_planning_is_staggered_and_new_arrivals_wait():
    simulation = Simulation()
    policy = ModelPolicy(simulation, model="fake", clock=lambda: 0)
    try:
        slots = list(policy.next_request_tick.values())
        assert len(set(slots)) == len(simulation.agents)
        assert 5 <= min(slots) <= max(slots) <= 50
        assert not any(policy._eligible(agent) for agent in simulation.agents)
        simulation.tick = slots[0]
        assert policy._eligible(simulation.agents[0])
        assert not policy._eligible(simulation.agents[1])
        simulation.tick = 1000
        simulation._spawn_survivor()
        newcomer = simulation.agents[-1]
        assert not policy._eligible(newcomer)
        assert 1005 <= policy.next_request_tick[newcomer.id] <= 1010
    finally:
        policy.close()


def test_both_tick_and_wall_limits_apply_even_after_failure():
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    agent.intelligence = 100
    now = [0.0]
    policy = ModelPolicy(simulation, model="fake", clock=lambda: now[0])
    try:
        policy._defer(agent)
        tick = policy.next_request_tick[agent.id]
        assert 50 <= tick <= 75
        assert policy.next_request[agent.id] == 20
        policy._invalidate(agent, "tool failure")
        simulation.tick = tick
        assert not policy._eligible(agent)  # Ticks alone are insufficient.
        now[0] = 20
        simulation.tick = tick - 1
        assert not policy._eligible(agent)  # Wall time alone is insufficient.
        simulation.tick = tick
        assert policy._eligible(agent)
    finally:
        policy.close()


def test_intelligence_controls_both_limits_and_jitter_stays_bounded():
    simulation = Simulation(agents=2)
    smart, slow = simulation.agents
    smart.intelligence, slow.intelligence = 100, 0
    policy = ModelPolicy(simulation, model="fake", clock=lambda: 0)
    try:
        for _ in range(100):
            policy._defer(smart)
            policy._defer(slow)
            assert 50 <= policy.next_request_tick[smart.id] <= 52
            assert 73 <= policy.next_request_tick[slow.id] <= 75
            assert policy.next_request[smart.id] == 20
            assert policy.next_request[slow.id] == 40
        assert len(simulation.trace.events_named("model.eligibility_scheduled")) == 200
    finally:
        policy.close()


def test_live_request_path_obeys_initial_slot_and_both_followup_limits():
    simulation = Simulation(agents=1)
    agent = simulation.agents[0]
    now, calls = [0.0], []

    def requester(*args):
        calls.append(args)
        return {"goal": "Survey", "milestones": ["explore"]}

    policy = ModelPolicy(simulation, model="fake", cooldown=0,
                         clock=lambda: now[0], requester=requester)
    try:
        policy._maybe_request(agent)
        assert not policy.pending
        simulation.tick = policy.next_request_tick[agent.id]
        policy._maybe_request(agent)
        policy.pending[agent.id][1].result(timeout=2)
        policy._consume()
        assert len(calls) == 1
        assert calls[0][2]["intelligence"] == agent.intelligence
        policy._invalidate(agent, "new water")
        due_tick = policy.next_request_tick[agent.id]
        simulation.tick = due_tick
        policy._maybe_request(agent)
        assert not policy.pending
        now[0] = policy.next_request[agent.id]
        simulation.tick = due_tick - 1
        policy._maybe_request(agent)
        assert not policy.pending
        simulation.tick = due_tick
        agent.inventory["wood"] += 1  # Avoid a cache hit for identical state.
        policy._maybe_request(agent)
        policy.pending[agent.id][1].result(timeout=2)
        assert len(calls) == 2
    finally:
        policy.close()

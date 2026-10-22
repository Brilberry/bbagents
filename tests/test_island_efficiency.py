import json

from bbagents import StepStatus
from examples.survival_island import Simulation
from examples.survival_island.inference import InferenceGate
from examples.survival_island.model_policy import ModelPolicy
from examples.survival_island.reporting import ReportManager


def test_one_model_plan_drives_many_tool_actions():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    simulation.world.tile(agent.position).resources["wood"] = 20
    calls = []

    def requester(*args):
        calls.append(args)
        return {"goal": "Prepare a weapon", "milestones": ["gather_wood", "craft_spear"]}

    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, clock=lambda: 0, requester=requester)
    simulation.policy = policy
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        for _ in range(15):
            simulation.step()
        plan = policy.model_plans[agent.id]
        assert plan.is_complete()
        assert agent.inventory["spear"] == 1
        assert len(plan.steps[0].evidence) == 10
        assert plan.steps[1].depends_on == [plan.steps[0].id]
        assert len(calls) == 1
        assert sum(event.attributes.get("source") == "model plan"
                   for event in simulation.trace.events_named("tool.started")) >= 11
    finally:
        policy.close()


def test_global_inference_gate_inserts_idle_time_after_completion():
    now = [0.0]
    starts = []

    def sleep(seconds):
        now[0] += seconds

    def request():
        starts.append(now[0])
        now[0] += 2  # simulated inference duration

    gate = InferenceGate(3, clock=lambda: now[0], sleeper=sleep)
    gate.run(request)
    assert not gate.ready()
    gate.run(request)
    assert starts == [0, 5]


def test_material_change_replans_but_not_every_tick():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    now = [0.0]
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, clock=lambda: now[0], requester=lambda *args: {
        "goal": "Explore", "milestones": ["explore"]})
    simulation.policy = policy
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        simulation.step()
        plan = policy.model_plans[agent.id]
        agent.home = agent.position
        policy.decide(agent)
        assert agent.id in policy.reasons
        assert not policy.pending
        assert policy.model_plans[agent.id] is plan
        now[0] = 41
        policy.decide(agent)
        assert agent.id in policy.pending
        policy.pending[agent.id][1].result(timeout=2)
        policy.decide(agent)
        assert policy.model_plans[agent.id].version == 2
    finally:
        policy.close()


def test_strategy_cache_is_bounded_and_inventory_sensitive():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=lambda *args: None)
    try:
        key = policy._cache_key(policy._snapshot(agent))
        policy.plan_cache.put(key, {"goal": "Recover", "milestones": ["rest"]})
        policy.decide(agent)
        assert policy.cache_hits == 1
        assert policy.gate.calls == 0
        agent.inventory["berries"] = 1
        assert policy._cache_key(policy._snapshot(agent)) != key
    finally:
        policy.close()


def test_streamed_trace_keeps_recent_memory_and_complete_disk_history(tmp_path):
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    manager = ReportManager(simulation, directory=tmp_path)
    for tick in range(1, 1001):
        simulation.tick = tick
        simulation.trace.record("test.tick", {"agent": "agent-1"})
    assert len(simulation.trace.events) <= 200
    assert min(event.attributes["recorded_tick"] for event in simulation.trace.events) == 801
    manager.close()
    rows = [json.loads(line) for line in (tmp_path / "agent-1.jsonl").read_text().splitlines()]
    assert any(row.get("tick") == 1 for row in rows)
    assert any(row.get("tick") == 1000 for row in rows)
    assert len(rows) > len(simulation.trace.events)


def test_late_postmortem_uses_disk_history_after_memory_eviction(tmp_path):
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    requests = []

    def requester(endpoint, model, request):
        requests.append(request)
        return {"summary": "Starvation at tick 1", "likely_contributors": [],
                "uncertainties": [], "evidence_ticks": [1]}

    manager = ReportManager(simulation, directory=tmp_path, model="fake", requester=requester)
    agent = simulation.agents[0]
    agent.health, agent.hunger = 1, 95
    simulation.step()
    simulation.tick = 300
    simulation.trace.record("heartbeat", {"agent": agent.id})
    assert not simulation.trace.events_named("survivor.died")
    manager.close()
    assert len(requests) == 1
    assert "survivor.died" in requests[0].context.content
    assert '"event": "heartbeat"' not in requests[0].context.content
    assert "Starvation at tick 1" in (tmp_path / "agent-1.md").read_text()


def test_survival_emergency_pauses_instead_of_losing_model_plan():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=lambda *args: {
        "goal": "Explore", "milestones": ["explore"]})
    simulation.policy = policy
    try:
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        simulation.step()
        plan = policy.model_plans[agent.id]
        agent.hunger = 70
        agent.inventory["berries"] = 3
        simulation.step()
        assert agent.decision_source == "rule"
        assert policy.model_plans[agent.id] is plan
        for _ in range(5):
            simulation.step()
        assert plan.steps[0].status == StepStatus.PENDING
        assert len(plan.steps[0].evidence) >= 2
    finally:
        policy.close()


def test_only_one_gameplay_request_can_be_outstanding():
    from threading import Event
    release = Event()
    simulation = Simulation(predator_interval=None, lightning_probability=0)

    def requester(*args):
        assert release.wait(timeout=3)
        return {"goal": "Explore", "milestones": ["explore"]}

    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", cooldown=0, requester=requester)
    try:
        for agent in simulation.agents:
            policy.decide(agent)
        assert len(policy.pending) == 1
        release.set()
        next(iter(policy.pending.values()))[1].result(timeout=2)
    finally:
        release.set()
        policy.close()


def test_postmortems_share_gameplay_inference_gate(tmp_path):
    now = [0.0]
    starts = []

    def sleep(seconds):
        now[0] += seconds

    def gameplay(*args):
        starts.append(("gameplay", now[0]))
        now[0] += 1
        return {"goal": "Explore", "milestones": ["explore"]}

    def summary(*args):
        starts.append(("summary", now[0]))
        return {"summary": "Starvation", "likely_contributors": [], "uncertainties": [], "evidence_ticks": [1]}

    gate = InferenceGate(3, clock=lambda: now[0], sleeper=sleep)
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    policy = ModelPolicy(simulation, initial_stagger=False, tick_range=(0, 0), model="fake", gate=gate, clock=lambda: now[0], requester=gameplay)
    simulation.policy = policy
    manager = ReportManager(simulation, directory=tmp_path, model="fake", requester=summary)
    try:
        agent = simulation.agents[0]
        policy.decide(agent)
        policy.pending[agent.id][1].result(timeout=2)
        agent.health, agent.hunger = 1, 95
        simulation.step()
        manager.close()
        assert manager.gate is policy.gate
        assert starts == [("gameplay", 0), ("summary", 4)]
    finally:
        policy.close()

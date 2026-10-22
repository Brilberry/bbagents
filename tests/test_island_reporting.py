import json

from examples.survival_island import Simulation
from examples.survival_island.reporting import ReportManager, last_window, survivor_events
from examples.survival_island.simulation import Predator


def test_damage_and_death_are_attributed_once_to_survivor():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.health = 1
    agent.hunger = 95
    agent.thirst = 95
    simulation.step()
    simulation.step()
    deaths = simulation.trace.events_named("survivor.died")
    assert len(deaths) == 1
    assert agent.death_tick == 1
    assert agent.death_cause == "starvation, dehydration"
    damage = simulation.trace.events_named("survivor.damage")[0]
    assert damage.attributes["agent"] == agent.id
    assert damage.attributes["damage"] == 1
    assert damage.attributes["tick"] == 1


def test_predator_death_is_recorded_after_combat():
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.health = 1
    agent.energy = 0
    simulation.tick = 1
    simulation.predators.append(Predator("bear-test", "bear", agent.position, 150))
    simulation.step()
    assert agent.death_cause == "bear attack (bear-test)"
    assert agent.death_tick == 2
    assert simulation.trace.events_named("survivor.died")


def test_last_window_is_200_ticks_and_includes_received_messages():
    events = [{"agent": "a", "tick": tick, "event": "survivor.state"} for tick in range(301)]
    events += [{"agent": "b", "recipient": "a", "tick": 200, "event": "message.sent"},
               {"agent": "b", "tick": 200, "event": "tool.started"}]
    selected = last_window(survivor_events(events, "a"), 250)
    assert min(row["tick"] for row in selected) == 51
    assert max(row["tick"] for row in selected) == 250
    assert sum(row["event"] == "survivor.state" for row in selected) == 200
    assert any(row["event"] == "message.sent" for row in selected)
    assert not any(row["event"] == "tool.started" for row in selected)


def test_reports_save_per_survivor_logs_and_model_postmortem(tmp_path):
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.health, agent.hunger = 1, 95
    simulation.step()
    requests = []

    def requester(endpoint, model, request):
        requests.append(request)
        assert "starvation" in request.context.content
        return {"summary": "Ari died from starvation at tick 1.",
                "likely_contributors": ["Hunger was already critical."],
                "uncertainties": ["A different policy was not tested."], "evidence_ticks": [1]}

    manager = ReportManager(simulation, directory=tmp_path, model="fake", requester=requester)
    manager.update()
    manager.update()  # No duplicate analysis submissions.
    manager.close()
    assert len(requests) == 1
    assert "starvation" in (tmp_path / "agent-1.md").read_text()
    report = (tmp_path / "agent-1.md").read_text()
    assert "Ari died from starvation" in report
    assert "## Final 200 ticks" in report
    assert report.count("## Model postmortem:") == 1
    assert not list(tmp_path.glob("*.summary.md"))
    manager.save()  # Repeated exports retain the completed analysis and timeline.
    assert (tmp_path / "agent-1.md").read_text() == report
    assert "agent-1.md" in (tmp_path / "index.md").read_text()
    rows = [json.loads(line) for line in (tmp_path / "agent-1.jsonl").read_text().splitlines()]
    assert any(row["event"] == "survivor.died" for row in rows)
    assert all(row.get("agent") == agent.id or row.get("recipient") == agent.id for row in rows)
    assert simulation.trace.events_named("decision.completed")


def test_invalid_model_evidence_does_not_replace_raw_reports(tmp_path):
    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    agent = simulation.agents[0]
    agent.health, agent.hunger = 1, 95
    simulation.step()
    manager = ReportManager(simulation, directory=tmp_path, model="fake", requester=lambda *args: {
        "summary": "Unsupported claim", "likely_contributors": [], "uncertainties": [],
        "evidence_ticks": [999999]})
    manager.close()
    report = (tmp_path / "agent-1.md").read_text()
    assert "unavailable" in report
    assert "## Final 200 ticks" in report
    assert not list(tmp_path.glob("*.summary.md"))
    assert "survivor.died" in (tmp_path / "agent-1.jsonl").read_text()
    assert len(simulation.trace.events_named("decision.failed")) == 2


def test_no_model_still_produces_inspectable_histories(tmp_path):
    simulation = Simulation(predator_interval=None, lightning_probability=0)
    simulation.step()
    manager = ReportManager(simulation, directory=tmp_path)
    manager.close()
    assert (tmp_path / "trace.jsonl").exists()
    assert (tmp_path / "agent-5.md").exists()
    assert not list(tmp_path.glob("*.summary.md"))

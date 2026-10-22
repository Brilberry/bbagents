from types import SimpleNamespace

import pytest

from examples.survival_island import Simulation


def test_last_death_ends_run_and_future_steps_do_nothing():
    simulation = Simulation(agents=1, predator_interval=250, survivor_interval=1000)
    agent = simulation.agents[0]
    agent.health, agent.hunger = 1, 95
    simulation.step()
    assert simulation.game_over
    assert simulation.ended_tick == agent.death_tick == simulation.tick == 1
    assert len(simulation.trace.events_named("simulation.ended")) == 1
    events = len(simulation.trace.events)
    weather_state = simulation.weather_random.getstate()
    for _ in range(2500):
        simulation.step()
    assert simulation.tick == 1
    assert len(simulation.agents) == 1
    assert not simulation.predators
    assert len(simulation.trace.events) == events
    assert simulation.weather_random.getstate() == weather_state


def test_external_last_death_cannot_trigger_a_new_arrival():
    simulation = Simulation(agents=1)
    simulation.tick = 999
    simulation.agents[0].alive = False
    simulation.step()
    assert simulation.tick == simulation.ended_tick == 999
    assert len(simulation.agents) == 1
    assert simulation.agents[0].death_tick == 999


def test_one_remaining_survivor_keeps_the_game_running():
    simulation = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    simulation.agents[0].alive = False
    simulation.step()
    assert not simulation.game_over
    assert simulation.tick == 1
    assert simulation.ended_tick is None
    assert not simulation.trace.events_named("simulation.ended")


def test_renderer_freezes_final_state_and_saves_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    pygame = pytest.importorskip("pygame")
    from examples.survival_island import app

    simulation = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    simulation.agents[0].health, simulation.agents[0].hunger = 1, 95
    monkeypatch.setattr(app, "Simulation", lambda: simulation)
    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: 1000))
    frames = iter([[], [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
                        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT)],
                   [pygame.event.Event(pygame.QUIT)]])
    monkeypatch.setattr(pygame.event, "get", lambda: next(frames))
    app.run(report_dir=str(tmp_path), postmortems=False)
    assert simulation.tick == simulation.ended_tick == 1
    assert "Game over at tick 1" in capsys.readouterr().out
    assert "Died at tick 1" in (tmp_path / "agent-1.md").read_text()
    assert "simulation.ended" in (tmp_path / "trace.jsonl").read_text()

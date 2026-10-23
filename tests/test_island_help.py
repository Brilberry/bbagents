from types import SimpleNamespace

import pytest

from examples.survival_island import Simulation


@pytest.mark.parametrize("dismiss", ["h", "escape", "button"])
def test_help_pauses_and_blocks_commands_then_resumes(monkeypatch, tmp_path, dismiss):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame = pytest.importorskip("pygame")
    from examples.survival_island import app
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    monkeypatch.setattr(app, "Simulation", lambda: sim)
    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: 1000))
    seen = []
    dismissal = (pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(800, 510))
                 if dismiss == "button" else pygame.event.Event(pygame.KEYDOWN,
                     key=pygame.K_h if dismiss == "h" else pygame.K_ESCAPE))
    frames = iter([
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
         pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT)],
        [dismissal],
        [pygame.event.Event(pygame.QUIT)],
    ])

    def events():
        seen.append(sim.tick)
        return next(frames)

    monkeypatch.setattr(pygame.event, "get", events)
    app.run(report_dir=str(tmp_path), postmortems=False)
    assert seen[:3] == [0, 0, 0]
    assert seen[3] == 8


def test_help_preserves_manual_pause(monkeypatch, tmp_path):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame = pytest.importorskip("pygame")
    from examples.survival_island import app
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    monkeypatch.setattr(app, "Simulation", lambda: sim)
    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: 1000))
    def key(value):
        return pygame.event.Event(pygame.KEYDOWN, key=value)
    frames = iter([[key(pygame.K_SPACE), key(pygame.K_h)], [key(pygame.K_ESCAPE)],
                   [key(pygame.K_RIGHT)], [pygame.event.Event(pygame.QUIT)]])
    monkeypatch.setattr(pygame.event, "get", lambda: next(frames))
    app.run(report_dir=str(tmp_path), postmortems=False)
    assert sim.tick == 1

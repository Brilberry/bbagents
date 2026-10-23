from threading import Event

import pytest

from examples.survival_island import Simulation
from examples.survival_island.reporting import ReportManager
from examples.survival_island.report_view import PostmortemExport, render_html


def ended():
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    sim.agents[0].health, sim.agents[0].hunger = 1, 95
    sim.step()
    return sim


def test_html_keeps_markdown_and_embeds_all_evidence_safely(tmp_path):
    source = tmp_path / "survival-report.md"
    source.write_text('''# Survival Island report
- [Ari](#agent-1)
<a id="agent-1"></a>
## Ari
<script>alert("bad")</script>
[bad](javascript:alert(1))
### Model postmortem
<!-- model-postmortem:agent-1 -->
Recorded cause: starvation.
<!-- /model-postmortem:agent-1 -->
```jsonl
{"message":"<img src=x onerror=alert(1)>"}
```
''')
    original = source.read_text()
    target = render_html(source)
    html = target.read_text()
    assert source.read_text() == original
    assert '<a href="#agent-1">Ari</a>' in html
    assert '<a id="agent-1"></a>' in html
    assert "<script>" not in html and "<img " not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert 'href="javascript:' not in html
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert "<details>" in html and "Recorded cause: starvation." in html


def test_export_waits_off_thread_for_model_and_opens_browser_once(tmp_path):
    entered, release = Event(), Event()

    def requester(*args):
        entered.set()
        assert release.wait(5)
        return {"summary": "Starvation explained.", "likely_contributors": [],
                "uncertainties": [], "evidence_ticks": [1]}

    manager = ReportManager(ended(), directory=tmp_path, model="fake", requester=requester)
    opened = []
    export = PostmortemExport(manager, opener=lambda uri: opened.append(uri) or True)
    try:
        export.start()
        assert entered.wait(5)
        first = export.future
        export.start()
        assert export.future is first and export.busy
        assert not opened
        release.set()
        export.future.result(timeout=5)
        export.poll()
        export.poll()
        assert len(opened) == 1
        assert opened[0] == (tmp_path / "survival-report.html").as_uri()
        assert "Starvation explained." in (tmp_path / "survival-report.md").read_text()
        assert "Starvation explained." in (tmp_path / "survival-report.html").read_text()
        assert manager.closed
    finally:
        release.set()
        export.finish()


def test_failed_html_generation_preserves_markdown_and_can_retry(monkeypatch, tmp_path):
    from examples.survival_island import report_view
    manager = ReportManager(ended(), directory=tmp_path)
    export = PostmortemExport(manager, opener=lambda uri: False)
    original = report_view.render_html
    monkeypatch.setattr(report_view, "render_html", lambda path: (_ for _ in ()).throw(OSError("disk unavailable")))
    try:
        export.start()
        with pytest.raises(OSError):
            export.future.result(timeout=5)
        export.poll()
        assert "disk unavailable" in export.error
        assert (tmp_path / "survival-report.md").exists()
        monkeypatch.setattr(report_view, "render_html", original)
        export.start()
        export.future.result(timeout=5)
        export.poll()
        assert export.path.exists()
        assert "browser unavailable" in export.message
    finally:
        export.finish()


def test_game_over_button_generates_and_opens_html(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame = pytest.importorskip("pygame")
    from examples.survival_island import app, report_view
    sim = Simulation(agents=1, predator_interval=None, lightning_probability=0)
    sim.agents[0].health, sim.agents[0].hunger = 1, 95
    monkeypatch.setattr(app, "Simulation", lambda: sim)
    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: 1000))
    opened = []
    monkeypatch.setattr(report_view.webbrowser, "open", lambda uri: opened.append(uri) or True)
    frame = 0

    def events():
        nonlocal frame
        frame += 1
        if frame == 1:
            return []
        if frame == 2:
            return [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(300, 400))]
        if opened:
            return [pygame.event.Event(pygame.QUIT)]
        assert frame < 1000, "postmortem export did not finish"
        Event().wait(0.005)
        return []

    monkeypatch.setattr(pygame.event, "get", events)
    app.run(report_dir=str(tmp_path), postmortems=False)
    assert sim.tick == 1
    assert len(opened) == 1
    assert (tmp_path / "survival-report.html").exists()

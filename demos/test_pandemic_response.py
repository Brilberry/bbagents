from demos.pandemic_response import PandemicResponseFixture, run_example
from bbagents.runtime import ActionStatus


def test_fixture_needs_human_before_effect(tmp_path):
    fixture = PandemicResponseFixture(tmp_path)
    result = fixture.respond()
    assert result.status == ActionStatus.PENDING_INTERVENTION
    assert fixture.effects.load("response-active") is None


def test_fixture_approval_restart_and_replay():
    run_example()

from pathlib import Path

from bbagents import ActionStatus
from demos.autonomous_release_manager import AutonomousReleaseManager, QualityCheck


def passing_check(name: str, command: tuple[str, ...], cwd: Path) -> QualityCheck:
    del command, cwd
    return QualityCheck(name, True, "passed")


def test_release_requires_approval_before_publication() -> None:
    manager = AutonomousReleaseManager(check_runner=passing_check)

    preflight = manager.preflight()
    pending = manager.publish()

    assert preflight.status == "ready_for_intervention"
    assert pending.status == ActionStatus.PENDING_INTERVENTION
    assert pending.intervention_request_id is not None
    assert manager.current().status == "awaiting_human_intervention"
    assert manager.published_versions == []

    manager.approve("release-approver")
    published = manager.publish()

    assert published.success
    assert manager.current().status == "released"
    assert manager.published_versions == [preflight.version]
    assert manager.trace.events_named("retrieval.completed")
    assert manager.capabilities.audit_log[-1]["allowed"] is True


def test_failed_validation_blocks_release_without_requesting_approval() -> None:
    def failing_check(name: str, command: tuple[str, ...], cwd: Path) -> QualityCheck:
        del command, cwd
        return QualityCheck(name, False, "failed")

    manager = AutonomousReleaseManager(check_runner=failing_check)
    assert manager.preflight().status == "blocked"
    result = manager.publish()
    assert result.status == ActionStatus.DENIED
    assert manager.intervention_channel.requests == []
    assert manager.published_versions == []

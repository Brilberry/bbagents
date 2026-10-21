from examples.oauth_migration_planner import run_example

from bbagents.planning import StepStatus


def test_oauth_migration_planner_completes_in_dependency_order() -> None:
    state, trace = run_example()

    assert state.completed
    assert all(step.status == StepStatus.DONE for step in state.plan.steps)
    assert state.observations[-1].source == "migration.strategy"
    assert "AuthMiddleware" in state.observations[-1].content
    assert "SessionStore" in state.observations[-1].content
    assert [
        event.payload["step_id"] for event in trace.events_named("step.started")
    ] == [
        "authentication",
        "sessions",
        "extension_points",
        "strategy",
    ]
    assert len(trace.events) == 18

from examples.model_routing_and_cache import run_example


def test_workloads_route_to_models_that_satisfy_their_constraints() -> None:
    result = run_example()

    assert result.planning.model == "deep-model"
    assert result.formatting.model == "fast-model"
    assert result.planning.estimated_cost > result.formatting.estimated_cost


def test_repeated_request_uses_cache_without_another_execution() -> None:
    result = run_example()

    assert not result.planning.cache_hit
    assert result.repeated_planning.cache_hit
    assert result.repeated_planning.estimated_cost == 0.0
    assert result.repeated_planning.value == result.planning.value
    assert result.executions == [
        "oauth-migration-plan:v1",
        "format-release-notes:v1",
    ]

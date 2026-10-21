"""Route workloads by quality and latency, then cache stable results."""

from __future__ import annotations

from dataclasses import dataclass

from bbagents.models import ModelProfile
from bbagents.optimization import Cache, route_model


@dataclass(frozen=True, slots=True)
class WorkRequest:
    """Constraints and token estimates for one model-backed operation."""

    key: str
    prompt: str
    input_tokens: int
    output_tokens: int
    minimum_quality: float
    maximum_latency: float | None = None


@dataclass(frozen=True, slots=True)
class RoutedResult:
    """The selected model, estimated cost, and generated value."""

    request_key: str
    model: str
    estimated_cost: float
    value: str
    cache_hit: bool = False


@dataclass(slots=True)
class OptimizationExample:
    """Inspectable output from all routed requests."""

    planning: RoutedResult
    formatting: RoutedResult
    repeated_planning: RoutedResult
    executions: list[str]


PROFILES = (
    ModelProfile(
        name="fast-model",
        cost_per_million_input_tokens=0.50,
        cost_per_million_output_tokens=1.50,
        latency_seconds=0.4,
        quality=0.72,
    ),
    ModelProfile(
        name="balanced-model",
        cost_per_million_input_tokens=2.00,
        cost_per_million_output_tokens=6.00,
        latency_seconds=1.8,
        quality=0.87,
    ),
    ModelProfile(
        name="deep-model",
        cost_per_million_input_tokens=8.00,
        cost_per_million_output_tokens=24.00,
        latency_seconds=7.0,
        quality=0.97,
    ),
)


def execute(
    request: WorkRequest,
    cache: Cache[str, RoutedResult],
    executions: list[str],
) -> RoutedResult:
    """Return a cached result or route and execute the request."""

    cached = cache.get(request.key)
    if cached is not None:
        return RoutedResult(
            request_key=cached.request_key,
            model=cached.model,
            estimated_cost=0.0,
            value=cached.value,
            cache_hit=True,
        )

    profile = route_model(
        PROFILES,
        minimum_quality=request.minimum_quality,
        maximum_latency=request.maximum_latency,
    )
    executions.append(request.key)
    result = RoutedResult(
        request_key=request.key,
        model=profile.name,
        estimated_cost=profile.estimate_cost(
            request.input_tokens,
            request.output_tokens,
        ),
        value=f"{profile.name} completed: {request.prompt}",
    )
    cache.put(request.key, result)
    return result


def run_example() -> OptimizationExample:
    """Route two workloads and repeat one to demonstrate caching."""

    cache: Cache[str, RoutedResult] = Cache()
    executions: list[str] = []
    planning_request = WorkRequest(
        key="oauth-migration-plan:v1",
        prompt="Plan a backward-compatible OAuth migration",
        input_tokens=12_000,
        output_tokens=2_000,
        minimum_quality=0.92,
        maximum_latency=10.0,
    )
    formatting_request = WorkRequest(
        key="format-release-notes:v1",
        prompt="Format validated release notes",
        input_tokens=1_500,
        output_tokens=500,
        minimum_quality=0.70,
        maximum_latency=1.0,
    )

    planning = execute(planning_request, cache, executions)
    formatting = execute(formatting_request, cache, executions)
    repeated_planning = execute(planning_request, cache, executions)
    return OptimizationExample(
        planning=planning,
        formatting=formatting,
        repeated_planning=repeated_planning,
        executions=executions,
    )


def main() -> None:
    result = run_example()

    for name, routed in (
        ("Planning", result.planning),
        ("Formatting", result.formatting),
        ("Repeated planning", result.repeated_planning),
    ):
        print(
            f"{name}: model={routed.model}, "
            f"cost=${routed.estimated_cost:.4f}, cache_hit={routed.cache_hit}"
        )
    print(f"Actual model executions: {len(result.executions)}")


if __name__ == "__main__":
    main()

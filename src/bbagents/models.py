"""Model catalog and selection contracts for heterogeneous agent systems."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Generic, Protocol, Sequence, TypeVar

from .core import NullTrace, TraceSink
from .decision import DecisionModel, DecisionRequest, DecisionResult, DecisionRuntime
from .organization import Role

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Application-supplied cost, latency, and quality estimates for a model."""

    name: str
    cost_per_million_input_tokens: float
    cost_per_million_output_tokens: float
    latency_seconds: float
    quality: float

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.cost_per_million_input_tokens
            + output_tokens * self.cost_per_million_output_tokens
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """A stable model identity, its advertised capabilities, and optional adapter."""

    id: str
    profile: ModelProfile
    model: DecisionModel | None = None


@dataclass(frozen=True, slots=True)
class SelectionConstraints:
    minimum_quality: float = 0.0
    maximum_latency_seconds: float | None = None
    excluded_model_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_quality <= 1:
            raise ValueError("minimum_quality must be between 0 and 1")
        if (
            self.maximum_latency_seconds is not None
            and self.maximum_latency_seconds <= 0
        ):
            raise ValueError("maximum_latency_seconds must be positive")


class ModelCatalog:
    """Application-owned model candidates keyed by stable model identity."""

    def __init__(self, candidates: Sequence[ModelCandidate] = ()) -> None:
        self._candidates: dict[str, ModelCandidate] = {}
        for candidate in candidates:
            self.register(candidate)

    def register(self, candidate: ModelCandidate) -> None:
        if candidate.id in self._candidates:
            raise ValueError(f"Model already registered: {candidate.id}")
        self._candidates[candidate.id] = candidate

    def get(self, model_id: str) -> ModelCandidate:
        try:
            return self._candidates[model_id]
        except KeyError as error:
            raise KeyError(f"Unknown model: {model_id}") from error

    def permitted(self, agent: "AgentProfile") -> tuple[ModelCandidate, ...]:
        return tuple(self.get(model_id) for model_id in agent.permitted_model_ids)


class ModelSelector(Protocol):
    """Select one candidate under explicit, portable constraints."""

    def select(
        self,
        candidates: Sequence[ModelCandidate],
        constraints: SelectionConstraints,
    ) -> ModelCandidate: ...


class CheapestEligibleModelSelector:
    """Reference selector that minimizes listed per-token cost."""

    def select(
        self,
        candidates: Sequence[ModelCandidate],
        constraints: SelectionConstraints,
    ) -> ModelCandidate:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.id not in constraints.excluded_model_ids
            and candidate.profile.quality >= constraints.minimum_quality
            and (
                constraints.maximum_latency_seconds is None
                or candidate.profile.latency_seconds
                <= constraints.maximum_latency_seconds
            )
        ]
        if not eligible:
            raise LookupError("No model satisfies the selection constraints")
        return min(
            eligible,
            key=lambda candidate: (
                candidate.profile.cost_per_million_input_tokens
                + candidate.profile.cost_per_million_output_tokens
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """A stable organizational role with an explicit set of permitted models."""

    id: str
    role: Role
    permitted_model_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.permitted_model_ids:
            raise ValueError("agent profiles require at least one permitted model")


@dataclass(frozen=True, slots=True)
class AgentDecisionResult(Generic[T]):
    agent_id: str
    model_id: str
    decision: DecisionResult[T]


class AgentDecisionRuntime:
    """Select a permitted executable model, then run a structured decision."""

    def __init__(
        self,
        catalog: ModelCatalog,
        selector: ModelSelector,
        *,
        trace: TraceSink | None = None,
    ) -> None:
        self.catalog = catalog
        self.selector = selector
        self.registry = catalog
        self.policy = selector
        self.trace = trace or NullTrace()

    def decide(
        self,
        agent: AgentProfile,
        request: DecisionRequest[T],
        *,
        excluded_model_ids: Collection[str] = (),
        retries: int = 0,
    ) -> AgentDecisionResult[T]:
        candidates = self.catalog.permitted(agent)
        requirements = request.requirements
        selected = self.selector.select(
            candidates,
            SelectionConstraints(
                minimum_quality=requirements.minimum_quality,
                maximum_latency_seconds=requirements.maximum_latency_seconds,
                excluded_model_ids=frozenset(excluded_model_ids),
            ),
        )
        if selected.id not in {candidate.id for candidate in candidates}:
            raise ValueError("Model selector selected a model not permitted for agent")
        if selected.model is None:
            raise ValueError(f"Model candidate {selected.id!r} has no decision adapter")
        self.trace.record(
            "agent.model.selected",
            {
                "agent_id": agent.id,
                "role": agent.role.name,
                "model_id": selected.id,
                "task": request.task,
            },
        )
        decision = DecisionRuntime(
            selected.model,
            trace=self.trace,
            model_id=selected.id,
        ).decide(request, retries=retries)
        return AgentDecisionResult(agent.id, selected.id, decision)


# Pre-0.2 compatibility names. New code should use the catalog/selector names.
ModelRegistration = ModelCandidate
ModelRegistry = ModelCatalog
ModelSelectionPolicy = ModelSelector
CheapestEligibleModelPolicy = CheapestEligibleModelSelector

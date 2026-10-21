"""Durable task progress and completion gates, without a scheduler."""

from dataclasses import asdict, dataclass, replace
import math
from typing import Any, Protocol

from .hooks import HookBoundary, HookContext, HookRunner
from .persistence import StateStore


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    id: str
    description: str
    verified: bool = False
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    objective: str
    criteria: tuple[AcceptanceCriterion, ...]
    completed_work: tuple[str, ...] = ()
    pending_actions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    attempts: int = 0
    elapsed_seconds: float = 0
    cost: float = 0
    no_progress_count: int = 0
    status: str = "active"


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    allowed: bool
    reason: str


class CompletionPolicy(Protocol):
    def decide(self, task: TaskRecord) -> CompletionDecision: ...


class EvidenceCompletionPolicy:
    def decide(self, task: TaskRecord) -> CompletionDecision:
        allowed = (
            bool(task.criteria)
            and all(
                criterion.verified and criterion.evidence_refs
                for criterion in task.criteria
            )
            and not task.pending_actions
        )
        return CompletionDecision(
            allowed, "Verified criteria" if allowed else "Missing completion evidence"
        )


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    max_attempts: int = 20
    max_elapsed_seconds: float = 3600
    max_cost: float = 10
    max_no_progress: int = 3

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value) or value <= 0
            for value in (
                self.max_attempts,
                self.max_elapsed_seconds,
                self.max_cost,
                self.max_no_progress,
            )
        ):
            raise ValueError("All budget limits must be finite and positive")

    def exhausted(self, task: TaskRecord) -> bool:
        return (
            task.attempts >= self.max_attempts
            or task.elapsed_seconds >= self.max_elapsed_seconds
            or task.cost >= self.max_cost
            or task.no_progress_count >= self.max_no_progress
        )


class TaskRepository:
    def __init__(self, store: StateStore[dict[str, Any]]) -> None:
        self.store = store

    def load(self, task_id: str) -> tuple[TaskRecord, int] | None:
        snapshot = self.store.load(task_id)
        if snapshot is None:
            return None
        data = dict(snapshot.value)
        data["criteria"] = tuple(
            AcceptanceCriterion(
                **{**item, "evidence_refs": tuple(item["evidence_refs"])}
            )
            for item in data["criteria"]
        )
        for key in (
            "completed_work",
            "pending_actions",
            "unresolved_questions",
            "artifact_refs",
        ):
            data[key] = tuple(data[key])
        return TaskRecord(**data), snapshot.version

    def save(self, task: TaskRecord, *, expected_version: int) -> int:
        return self.store.save(
            task.id, asdict(task), expected_version=expected_version
        ).version


class TaskLifecycle:
    """The host reports measured time/cost and reconciles pending actions on resume."""

    def __init__(
        self,
        repository: TaskRepository,
        *,
        budget: ExecutionBudget | None = None,
        completion: CompletionPolicy | None = None,
        hooks: HookRunner | None = None,
    ) -> None:
        self.repository = repository
        self.budget = budget or ExecutionBudget()
        self.completion = completion or EvidenceCompletionPolicy()
        self.hooks = hooks or HookRunner()

    def record_attempt(
        self,
        task: TaskRecord,
        *,
        expected_version: int,
        made_progress: bool,
        elapsed_seconds: float,
        cost: float = 0,
    ) -> TaskRecord:
        if task.status != "active" or self.budget.exhausted(task):
            raise ValueError("Task cannot start another attempt")
        if any(not math.isfinite(v) or v < 0 for v in (elapsed_seconds, cost)):
            raise ValueError("Time and cost increments must be finite and nonnegative")
        updated = replace(
            task,
            attempts=task.attempts + 1,
            elapsed_seconds=task.elapsed_seconds + elapsed_seconds,
            cost=task.cost + cost,
            no_progress_count=0 if made_progress else task.no_progress_count + 1,
        )
        if self.budget.exhausted(updated):
            updated = replace(updated, status="exhausted")
        self.repository.save(updated, expected_version=expected_version)
        return updated

    async def complete(self, task: TaskRecord, *, expected_version: int) -> TaskRecord:
        if task.status != "active":
            raise ValueError("Task is not active")
        await self.hooks.run(HookContext(HookBoundary.BEFORE_COMPLETE, {"task": task}))
        decision = self.completion.decide(task)
        if not decision.allowed:
            raise ValueError(decision.reason)
        completed = replace(task, status="complete")
        self.repository.save(completed, expected_version=expected_version)
        return completed

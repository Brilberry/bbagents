"""Portable evaluation evidence, graders, and repeated-trial reporting.

Applications run trials in isolated environments and provide actual outcome
snapshots. This module does not execute agents or infer state from their answers.
"""

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Generic, Mapping, Protocol, Sequence, TypeVar

from .observability import TraceEvent

OutcomeT = TypeVar("OutcomeT")


@dataclass(frozen=True, slots=True)
class TrialEvidence(Generic[OutcomeT]):
    case_id: str
    trial_id: str
    response: str
    transcript: tuple[TraceEvent, ...]
    outcome: OutcomeT
    configuration: Mapping[str, Any]
    duration_seconds: float
    cost: float | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and nonnegative")
        if self.cost is not None and (not isfinite(self.cost) or self.cost < 0):
            raise ValueError("cost must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class Grade:
    """None means insufficient evidence, distinct from passing or failing."""

    passed: bool | None
    reason: str
    score: float | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.score is not None and (
            not isfinite(self.score) or not 0 <= self.score <= 1
        ):
            raise ValueError("score must be finite and between 0 and 1")


class Grader(Protocol[OutcomeT]):
    def grade(self, trial: TrialEvidence[OutcomeT]) -> Grade: ...


@dataclass(frozen=True, slots=True)
class TrialGrades:
    case_id: str
    trial_id: str
    grades: Mapping[str, Grade] = field(default_factory=dict)

    @property
    def passed(self) -> bool | None:
        if any(grade.passed is False for grade in self.grades.values()):
            return False
        if not self.grades or any(
            grade.passed is None for grade in self.grades.values()
        ):
            return None
        return True


def grade_trial(
    trial: TrialEvidence[OutcomeT], graders: Mapping[str, Grader[OutcomeT]]
) -> TrialGrades:
    return TrialGrades(
        trial.case_id,
        trial.trial_id,
        {name: grader.grade(trial) for name, grader in graders.items()},
    )


@dataclass(frozen=True, slots=True)
class TrialReport:
    trials: int
    passed: int
    failed: int
    insufficient: int
    empirical_success_rate: float | None
    cases_with_any_success: int
    cases_with_all_successes: int


def report_trials(results: Sequence[TrialGrades]) -> TrialReport:
    """Report observed rates only, without assuming independent repetitions."""

    by_case: dict[str, list[bool | None]] = {}
    seen: set[tuple[str, str]] = set()
    for result in results:
        identity = (result.case_id, result.trial_id)
        if identity in seen:
            raise ValueError(f"Duplicate trial: {identity}")
        seen.add(identity)
        by_case.setdefault(result.case_id, []).append(result.passed)
    statuses = [result.passed for result in results]
    passed = sum(status is True for status in statuses)
    failed = sum(status is False for status in statuses)
    return TrialReport(
        len(results),
        passed,
        failed,
        sum(status is None for status in statuses),
        passed / len(results) if results else None,
        sum(any(status is True for status in case) for case in by_case.values()),
        sum(all(status is True for status in case) for case in by_case.values()),
    )

"""Reference dataset trials with application-owned isolated environments."""

from dataclasses import dataclass
import hashlib
import json
import math
from statistics import mean, pstdev
from typing import Any, Callable, ContextManager, Generic, Mapping, Sequence, TypeVar
from uuid import uuid4

from .evaluation import (
    Grade,
    Grader,
    TrialEvidence,
    TrialGrades,
    TrialReport,
    grade_trial,
    report_trials,
)

InputT = TypeVar("InputT")
EnvironmentT = TypeVar("EnvironmentT")
OutcomeT = TypeVar("OutcomeT")


@dataclass(frozen=True, slots=True)
class EvaluationCase(Generic[InputT]):
    id: str
    input: InputT
    split: str = "regression"


@dataclass(frozen=True, slots=True)
class Dataset(Generic[InputT]):
    id: str
    version: str
    cases: tuple[EvaluationCase[InputT], ...]

    def __post_init__(self) -> None:
        if (
            not self.id
            or not self.version
            or len({case.id for case in self.cases}) != len(self.cases)
        ):
            raise ValueError("Datasets require identity, version and unique cases")


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    dataset_id: str
    dataset_version: str
    configuration_fingerprint: str
    grades: tuple[TrialGrades, ...]
    summary: TrialReport
    mean_duration_seconds: float | None
    duration_stddev_seconds: float | None
    total_known_cost: float
    trials_without_cost: int


def run_dataset(
    dataset: Dataset[InputT],
    *,
    configuration: Mapping[str, Any],
    environment: Callable[[EvaluationCase[InputT]], ContextManager[EnvironmentT]],
    execute: Callable[
        [EvaluationCase[InputT], EnvironmentT, str, Mapping[str, Any]],
        TrialEvidence[OutcomeT],
    ],
    graders: Mapping[str, Grader[OutcomeT]],
    repetitions: int = 1,
    split: str = "regression",
) -> ExperimentReport:
    """Hosts must create/reset isolated state in each fixture and clean up on exit.

    Failures propagate after fixture cleanup. This is a reference synchronous
    evaluation loop, not an agent scheduler. Held-out cases run only when selected.
    """

    if repetitions < 1 or not graders:
        raise ValueError("Evaluation requires positive repetitions and graders")
    fingerprint = _fingerprint(configuration)
    results: list[TrialGrades] = []
    evidence: list[TrialEvidence[OutcomeT]] = []
    experiment_id = uuid4().hex
    for case in dataset.cases:
        if case.split != split:
            continue
        for repetition in range(repetitions):
            trial_id = f"{experiment_id}:{case.id}:{repetition}"
            with environment(case) as isolated:
                trial = execute(case, isolated, trial_id, configuration)
                if trial.case_id != case.id or trial.trial_id != trial_id:
                    raise ValueError("Runner returned mismatched trial identity")
                if _fingerprint(trial.configuration) != fingerprint:
                    raise ValueError("Runner returned mismatched configuration")
                results.append(grade_trial(trial, graders))
                evidence.append(trial)
    durations = [trial.duration_seconds for trial in evidence]
    return ExperimentReport(
        dataset.id,
        dataset.version,
        fingerprint,
        tuple(results),
        report_trials(results),
        mean(durations) if durations else None,
        pstdev(durations) if durations else None,
        sum(trial.cost for trial in evidence if trial.cost is not None),
        sum(trial.cost is None for trial in evidence),
    )


def _fingerprint(configuration: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(configuration),
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def success_interval(
    results: Sequence[TrialGrades],
    *,
    independent_trials: bool = False,
) -> tuple[float, float] | None:
    """95% Wilson interval, ONLY if caller justifies independent Bernoulli trials.

    Unknown grades are not demonstrated successes. Correlated repetitions of a
    task usually violate the independence assumption; default returns no interval.
    """

    if not independent_trials or not results:
        return None
    report = report_trials(results)
    n = report.trials
    p = report.passed / n
    z = 1.959963984540054
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    judge_version: str
    rubric_version: str
    compared: int
    agreements: int
    insufficient: int


def calibrate(
    pairs: Sequence[tuple[Grade, Grade]],
    *,
    judge_version: str,
    rubric_version: str,
) -> CalibrationReport:
    compared = [
        (judge, human)
        for judge, human in pairs
        if judge.passed is not None and human.passed is not None
    ]
    return CalibrationReport(
        judge_version,
        rubric_version,
        len(compared),
        sum(judge.passed == human.passed for judge, human in compared),
        len(pairs) - len(compared),
    )

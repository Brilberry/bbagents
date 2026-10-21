from contextlib import contextmanager

import pytest

from bbagents.compaction import CompactionGrader, ContinuationObservation
from bbagents.evaluation import Grade, TrialEvidence, TrialGrades
from bbagents.experiments import (
    Dataset,
    EvaluationCase,
    calibrate,
    run_dataset,
    success_interval,
)


class OutcomeGrader:
    def grade(self, trial):
        return Grade(trial.outcome == ["effect"], "Verified isolated state")


def test_repetitions_are_isolated_heldout_excluded_and_cleanup_runs():
    cleaned = []

    @contextmanager
    def fixture(case):
        state = []
        try:
            yield state
        finally:
            cleaned.append(case.id)
            state.clear()

    def execute(case, state, trial_id, configuration):
        assert state == []
        state.append("effect")
        return TrialEvidence(
            case.id, trial_id, "done", (), state, configuration, 0.1, 0.02
        )

    dataset = Dataset(
        "checks",
        "1",
        (EvaluationCase("one", "input"), EvaluationCase("held", "input", "heldout")),
    )
    report = run_dataset(
        dataset,
        configuration={"model": "local", "environment": "v1"},
        environment=fixture,
        execute=execute,
        graders={"outcome": OutcomeGrader()},
        repetitions=2,
    )
    assert report.summary.passed == 2 and cleaned == ["one", "one"]
    assert report.total_known_cost == 0.04
    assert report.dataset_version == "1"


def test_fixture_cleanup_on_configuration_mismatch_and_runner_failure():
    cleaned = []

    @contextmanager
    def fixture(case):
        try:
            yield None
        finally:
            cleaned.append(True)

    def mismatch(case, state, trial_id, configuration):
        return TrialEvidence(case.id, trial_id, "", (), None, {"changed": True}, 0)

    def failed(*args):
        raise RuntimeError("runner failed")

    for execute in (mismatch, failed):
        with pytest.raises((ValueError, RuntimeError)):
            run_dataset(
                Dataset("d", "1", (EvaluationCase("one", ""),)),
                configuration={},
                environment=fixture,
                execute=execute,
                graders={"g": OutcomeGrader()},
            )
    assert len(cleaned) == 2


def test_compaction_probe_checks_constraints_replay_and_evidence():
    grader = CompactionGrader(
        frozenset({"approval"}), frozenset({"published"}), frozenset({"tests:1"})
    )
    bad = TrialEvidence(
        "c",
        "t",
        "looks concise",
        (),
        ContinuationObservation(frozenset(), frozenset({"published"}), frozenset()),
        {},
        0,
    )
    assert grader.grade(bad).passed is False
    good = TrialEvidence(
        "c",
        "t",
        "",
        (),
        ContinuationObservation(
            frozenset({"approval"}), frozenset(), frozenset({"tests:1"})
        ),
        {},
        0,
    )
    assert grader.grade(good).passed is True


def test_uncertainty_requires_explicit_independence_and_calibration_tracks_abstention():
    result = TrialGrades("c", "t", {"g": Grade(True, "yes")})
    assert success_interval([result]) is None
    low, high = success_interval([result], independent_trials=True)
    assert 0 < low < high <= 1
    report = calibrate(
        [
            (Grade(True, "yes"), Grade(False, "no")),
            (Grade(None, "missing"), Grade(True, "yes")),
        ],
        judge_version="j1",
        rubric_version="r1",
    )
    assert (report.compared, report.agreements, report.insufficient) == (1, 0, 1)

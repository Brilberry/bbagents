import pytest

from bbagents.evaluation import (
    Grade,
    TrialEvidence,
    TrialGrades,
    grade_trial,
    report_trials,
)


class BookingGrader:
    def grade(self, trial: TrialEvidence[dict[str, bool]]) -> Grade:
        return Grade(trial.outcome["booked"], "Checked the booking database")


def test_outcome_is_checked_independently_of_response() -> None:
    trial = TrialEvidence(
        "booking",
        "trial-1",
        "Booked successfully!",
        (),
        {"booked": False},
        {"model": "local", "skill_version": "2", "environment": "snapshot-1"},
        0.2,
    )
    result = grade_trial(trial, {"booking": BookingGrader()})
    assert result.passed is False


def test_report_distinguishes_any_success_all_success_and_missing_evidence() -> None:
    results = [
        TrialGrades("a", "1", {"outcome": Grade(True, "verified")}),
        TrialGrades("a", "2", {"outcome": Grade(False, "missing")}),
        TrialGrades("b", "1", {"outcome": Grade(None, "unavailable")}),
        TrialGrades("c", "1", {"outcome": Grade(True, "verified")}),
    ]
    report = report_trials(results)
    assert (report.passed, report.failed, report.insufficient) == (2, 1, 1)
    assert report.empirical_success_rate == 0.5
    assert report.cases_with_any_success == 2
    assert report.cases_with_all_successes == 1
    with pytest.raises(ValueError, match="Duplicate trial"):
        report_trials([results[0], results[0]])
    assert report_trials([]).empirical_success_rate is None

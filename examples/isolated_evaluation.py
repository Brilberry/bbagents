"""Two repetitions each get fresh environment state; answers are not outcomes."""

from contextlib import contextmanager

from bbagents.evaluation import Grade, TrialEvidence
from bbagents.experiments import Dataset, EvaluationCase, run_dataset


@contextmanager
def environment(case):
    state = {}
    try:
        yield state
    finally:
        state.clear()


def execute(case, state, trial_id, configuration):
    state["verified"] = True
    return TrialEvidence(
        case.id, trial_id, "done", (), dict(state), configuration, 0.01, 0
    )


class Verified:
    def grade(self, trial):
        return Grade(trial.outcome.get("verified") is True, "Environment checked")


def run_example() -> None:
    report = run_dataset(
        Dataset("verification", "1", (EvaluationCase("check", "verify"),)),
        configuration={"model": "fixture", "policy": "1", "environment": "clean"},
        environment=environment,
        execute=execute,
        graders={"verified": Verified()},
        repetitions=2,
    )
    assert report.summary.passed == 2
    print(report.summary)


if __name__ == "__main__":
    run_example()

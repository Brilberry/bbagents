"""Grade structured continuation probes rather than compression ratios."""

from dataclasses import dataclass

from .evaluation import Grade, TrialEvidence


@dataclass(frozen=True, slots=True)
class ContinuationObservation:
    retained_constraints: frozenset[str]
    proposed_action_ids: frozenset[str]
    recovered_evidence_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class CompactionGrader:
    required_constraints: frozenset[str]
    completed_action_ids: frozenset[str]
    required_evidence_refs: frozenset[str]

    def grade(self, trial: TrialEvidence[ContinuationObservation | None]) -> Grade:
        observation = trial.outcome
        if observation is None:
            return Grade(None, "No continuation evidence")
        missing_constraints = (
            self.required_constraints - observation.retained_constraints
        )
        repeated = self.completed_action_ids & observation.proposed_action_ids
        missing_evidence = (
            self.required_evidence_refs - observation.recovered_evidence_refs
        )
        passed = not (missing_constraints or repeated or missing_evidence)
        return Grade(
            passed,
            f"Missing constraints: {sorted(missing_constraints)}; "
            f"repeated actions: {sorted(repeated)}; missing evidence: {sorted(missing_evidence)}",
            evidence_refs=tuple(sorted(observation.recovered_evidence_refs)),
        )

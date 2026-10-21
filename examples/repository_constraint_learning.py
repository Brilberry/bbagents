"""Learn a repository constraint from validated authentication changes."""

from __future__ import annotations

from dataclasses import dataclass

from bbagents.learning import Experience, LearningRuntime, Procedure
from bbagents.memory import MemoryKind, MemoryRecord, MemoryStore


@dataclass(frozen=True, slots=True)
class RepositoryConstraint:
    """A reusable rule scoped to one area of a repository."""

    scope: str
    rule: str


EXPERIENCES = (
    Experience(
        task="Add passwordless authentication",
        outcome="Handlers changed, but authorization registry was omitted",
        success=False,
        evidence=("integration test rejected an unregistered auth handler",),
    ),
    Experience(
        task="Add SAML authentication",
        outcome="Handlers and authorization registry changed together",
        success=True,
        evidence=("authentication integration tests passed",),
    ),
    Experience(
        task="Add OAuth authentication",
        outcome="Handlers and authorization registry changed together",
        success=True,
        evidence=("OAuth callback and authorization tests passed",),
    ),
)


def generalize(experiences: list[Experience]) -> Procedure[RepositoryConstraint]:
    """Propose the narrow rule supported by successful executions."""

    return Procedure(
        name="authentication_requires_authorization_registration",
        value=RepositoryConstraint(
            scope="authentication",
            rule=(
                "Authentication handler changes require a corresponding "
                "authorization registry update."
            ),
        ),
        confidence=min(0.5 + 0.2 * len(experiences), 0.95),
    )


def validate(candidate: Procedure[RepositoryConstraint]) -> bool:
    """Accept only a scoped rule that names both sides of the constraint."""

    value = candidate.value
    normalized = value.rule.casefold()
    return (
        value.scope == "authentication"
        and "authentication" in normalized
        and "authorization registry" in normalized
    )


def build_runtime() -> tuple[MemoryStore, LearningRuntime[RepositoryConstraint]]:
    """Construct independent memory and learning components."""

    return MemoryStore(), LearningRuntime(generalize, validate)


def run_example() -> tuple[
    MemoryStore,
    LearningRuntime[RepositoryConstraint],
    Procedure[RepositoryConstraint],
]:
    """Record episodes, learn a validated rule, and promote it to memory."""

    memory, learner = build_runtime()
    for experience in EXPERIENCES:
        memory.remember(
            MemoryRecord(
                content=f"{experience.task}: {experience.outcome}",
                kind=MemoryKind.EPISODIC,
                confidence=1.0 if experience.success else 0.6,
                source="execution",
                metadata={
                    "success": experience.success,
                    "evidence": experience.evidence,
                },
            )
        )
        learner.observe(experience)

    procedure = learner.learn(minimum_experiences=2)
    if procedure is None:
        raise RuntimeError("Expected corroborating experiences to produce a procedure")

    memory.remember(
        MemoryRecord(
            content=procedure.value.rule,
            kind=MemoryKind.SEMANTIC,
            confidence=procedure.confidence,
            source="validated-learning",
            metadata={
                "procedure": procedure.name,
                "supporting_experiences": len(procedure.supporting_experiences),
            },
        )
    )
    return memory, learner, procedure


def main() -> None:
    memory, learner, procedure = run_example()
    results = memory.retrieve("authentication authorization registry", limit=3)

    print(f"Recorded experiences: {len(learner.experiences)}")
    print(f"Promoted procedure: {procedure.name}")
    print(f"Confidence: {procedure.confidence:.2f}")
    print(f"Rule: {procedure.value.rule}")
    print("\nRetrieved memory:")
    for result in results:
        print(f"- [{result.record.kind.value}] {result.record.content}")


if __name__ == "__main__":
    main()

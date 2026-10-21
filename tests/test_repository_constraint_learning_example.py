from examples.repository_constraint_learning import (
    EXPERIENCES,
    RepositoryConstraint,
    build_runtime,
    run_example,
)

from bbagents.memory import MemoryKind


def test_repository_constraint_is_promoted_after_corroboration() -> None:
    memory, learner, procedure = run_example()

    assert procedure.name in learner.procedures
    assert procedure.value == RepositoryConstraint(
        scope="authentication",
        rule=(
            "Authentication handler changes require a corresponding authorization "
            "registry update."
        ),
    )
    assert len(procedure.supporting_experiences) == 2
    assert any(record.kind == MemoryKind.SEMANTIC for record in memory)


def test_failed_episode_alone_cannot_promote_a_constraint() -> None:
    _, learner = build_runtime()
    learner.observe(EXPERIENCES[0])

    assert learner.learn(minimum_experiences=2) is None
    assert not learner.procedures

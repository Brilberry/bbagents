"""Validated experience-to-procedure learning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Experience:
    task: str
    outcome: str
    success: bool
    evidence: tuple[str, ...] = ()


@dataclass(slots=True)
class Procedure(Generic[T]):
    name: str
    value: T
    confidence: float = 0.5
    supporting_experiences: list[Experience] = field(default_factory=list)
    version: str = "1"
    owner: str = "application"
    scope: str = "application"


Generalizer = Callable[[list[Experience]], Procedure[T]]
Validator = Callable[[Procedure[T]], bool]


class LearningRuntime(Generic[T]):
    """Promotes a generalized procedure only after explicit validation."""

    def __init__(self, generalize: Generalizer[T], validate: Validator[T]) -> None:
        self.generalize = generalize
        self.validate = validate
        self.experiences: list[Experience] = []
        self.procedures: dict[str, Procedure[T]] = {}

    def observe(self, experience: Experience) -> None:
        self.experiences.append(experience)

    def learn(self, *, minimum_experiences: int = 2) -> Procedure[T] | None:
        successful = [experience for experience in self.experiences if experience.success]
        if len(successful) < minimum_experiences:
            return None
        candidate = self.generalize(successful)
        if not self.validate(candidate):
            return None
        candidate.supporting_experiences = list(successful)
        self.procedures[candidate.name] = candidate
        return candidate

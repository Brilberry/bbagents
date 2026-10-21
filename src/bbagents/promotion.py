"""Audited procedure validation, activation and rollback over revisions."""

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from .learning import Procedure
from .revisions import Revision, RevisionRepository

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    approved: bool
    reason: str
    evidence_refs: tuple[str, ...]


class ProcedurePromoter(Generic[T]):
    """The application validator supplies evaluation and/or approval evidence."""

    def __init__(
        self,
        repository: RevisionRepository[dict[str, Any]],
        validate: Callable[[Procedure[T]], PromotionDecision],
        *,
        encode: Callable[[T], Any],
    ) -> None:
        self.repository = repository
        self.validate = validate
        self.encode = encode

    def promote(
        self, procedure: Procedure[T], *, expected_revision: int
    ) -> Revision[dict[str, Any]]:
        if procedure.scope != self.repository.context.scope:
            raise PermissionError("Procedure scope differs from trusted context")
        decision = self.validate(procedure)
        if decision.approved and not decision.evidence_refs:
            raise ValueError("Promotion requires validation evidence")
        return self.repository.append(
            procedure.name,
            {
                "value": self.encode(procedure.value),
                "version": procedure.version,
                "owner": procedure.owner,
                "scope": procedure.scope,
                "approved": decision.approved,
            },
            expected_revision=expected_revision,
            reason=decision.reason,
            evidence_refs=decision.evidence_refs,
            activate=decision.approved,
        )

    def rollback(
        self, name: str, revision: int, *, expected_active: int, reason: str
    ) -> None:
        history = self.repository.history(name)
        if (
            not 1 <= revision <= len(history)
            or not history[revision - 1].value["approved"]
        ):
            raise ValueError("Cannot activate an unapproved procedure")
        self.repository.activate(
            name, revision, expected_active=expected_active, reason=reason
        )

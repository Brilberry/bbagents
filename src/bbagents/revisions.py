"""Scoped, auditable revisions over a transactional StateStore."""

from dataclasses import dataclass
from copy import deepcopy
import json
from typing import Any, Generic, Mapping, Protocol, TypeVar

from .persistence import ConcurrentWriteError, StateStore

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Trusted host identity/scope; never construct this from model metadata."""

    subject_id: str
    scope: str


class AccessPolicy(Protocol):
    def permits(self, context: AccessContext, operation: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Revision(Generic[T]):
    number: int
    value: T
    actor: str
    reason: str
    evidence_refs: tuple[str, ...]


class RevisionRepository(Generic[T]):
    """One CAS-protected aggregate per scope; use separate namespaces per domain.

    JSON-compatible values are retained, including rejected candidates. Activation
    and rollback require policy independently of writing candidate revisions.
    This reference design favors correctness over large-corpus performance.
    """

    def __init__(
        self,
        store: StateStore[dict[str, Any]],
        context: AccessContext,
        policy: AccessPolicy,
        *,
        namespace: str,
    ) -> None:
        self.store = store
        self.context = context
        self.policy = policy
        self.key = json.dumps([namespace, context.scope], separators=(",", ":"))

    def authorize(self, operation: str) -> None:
        if not self.policy.permits(self.context, operation):
            raise PermissionError(f"Scope access denied: {operation}")

    def _load(self) -> tuple[dict[str, Any], int]:
        snapshot = self.store.load(self.key)
        return (
            (deepcopy(dict(snapshot.value)), snapshot.version) if snapshot else ({}, 0)
        )

    def current(self, item_id: str) -> Revision[T] | None:
        self.authorize("read")
        data, _ = self._load()
        entry = data.get(item_id)
        if not entry or entry["active"] is None:
            return None
        return self._revision(entry["history"][entry["active"] - 1])

    def items(self) -> tuple[tuple[str, Revision[T]], ...]:
        self.authorize("read")
        data, _ = self._load()
        return tuple(
            (key, self._revision(entry["history"][entry["active"] - 1]))
            for key, entry in data.items()
            if entry["active"] is not None
        )

    def history(self, item_id: str) -> tuple[Revision[T], ...]:
        self.authorize("read")
        data, _ = self._load()
        return tuple(
            self._revision(item) for item in data.get(item_id, {}).get("history", [])
        )

    def append(
        self,
        item_id: str,
        value: T,
        *,
        expected_revision: int,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
        activate: bool = False,
    ) -> Revision[T]:
        self.authorize("write")
        if activate:
            self.authorize("activate")
        data, version = self._load()
        entry = data.setdefault(item_id, {"history": [], "active": None, "events": []})
        actual = len(entry["history"])
        if actual != expected_revision:
            raise ConcurrentWriteError(f"Revision is {actual}, not {expected_revision}")
        revision = Revision(
            actual + 1, value, self.context.subject_id, reason, evidence_refs
        )
        entry["history"].append(
            {
                "number": revision.number,
                "value": value,
                "actor": revision.actor,
                "reason": reason,
                "evidence_refs": list(evidence_refs),
            }
        )
        if activate:
            entry["active"] = revision.number
            entry["events"].append(
                {"revision": revision.number, "actor": revision.actor, "reason": reason}
            )
        self.store.save(self.key, data, expected_version=version)
        return revision

    def activate(
        self,
        item_id: str,
        revision: int | None,
        *,
        expected_active: int | None,
        reason: str,
    ) -> None:
        self.authorize("activate")
        data, version = self._load()
        entry = data[item_id]
        if entry["active"] != expected_active:
            raise ConcurrentWriteError("Active revision changed")
        if revision is not None and not 1 <= revision <= len(entry["history"]):
            raise ValueError("Unknown revision")
        entry["active"] = revision
        entry["events"].append(
            {"revision": revision, "actor": self.context.subject_id, "reason": reason}
        )
        self.store.save(self.key, data, expected_version=version)

    def audit(self, item_id: str) -> tuple[Mapping[str, Any], ...]:
        self.authorize("read")
        data, _ = self._load()
        return tuple(data[item_id]["events"])

    @staticmethod
    def _revision(data: Mapping[str, Any]) -> Revision[T]:
        return Revision(
            data["number"],
            data["value"],
            data["actor"],
            data["reason"],
            tuple(data["evidence_refs"]),
        )

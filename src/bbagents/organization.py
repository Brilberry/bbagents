"""Typed organizational messages, roles, tasks, and governance policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    responsibilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Message:
    sender: str
    recipient: str
    content: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class OrganizationalTask:
    id: str
    objective: str
    owner: str | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    name: str
    predicate: Callable[[OrganizationalTask], bool]
    explanation: str

    def permits(self, task: OrganizationalTask) -> bool:
        return self.predicate(task)


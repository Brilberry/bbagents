"""Portable source and trust labels for data crossing agent-system boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Mapping


class TrustLevel(IntEnum):
    UNTRUSTED = 0
    EXTERNAL = 1
    INTERNAL = 2
    SYSTEM = 3


@dataclass(frozen=True, slots=True)
class Provenance:
    """Attributable origin and trust label for an item of agent-visible content."""

    source_id: str
    trust: TrustLevel
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def system(cls, source_id: str) -> "Provenance":
        return cls(source_id, TrustLevel.SYSTEM)

    @classmethod
    def internal(cls, source_id: str) -> "Provenance":
        return cls(source_id, TrustLevel.INTERNAL)

    @classmethod
    def external(cls, source_id: str) -> "Provenance":
        return cls(source_id, TrustLevel.EXTERNAL)

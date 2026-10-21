"""Versioned authored guidance; discovery and loading remain application adapters."""

from dataclasses import dataclass
from typing import Protocol

from .context import ContextItem
from .provenance import Provenance


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """Small discovery record, safe to list without loading full instructions.

    Required tools describe dependencies and never grant execution authority.
    Scope and provenance are claims for application policy to verify.
    """

    id: str
    version: str
    owner: str
    scope: str
    description: str
    provenance: Provenance
    required_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Skill:
    descriptor: SkillDescriptor
    instructions: str
    resource_refs: tuple[str, ...] = ()

    def context_item(self, *, priority: int = 0) -> ContextItem:
        return ContextItem(
            self.instructions,
            self.descriptor.id,
            priority=priority,
            metadata={
                "skill_version": self.descriptor.version,
                "owner": self.descriptor.owner,
                "scope": self.descriptor.scope,
                "resource_refs": self.resource_refs,
            },
            provenance=self.descriptor.provenance,
        )


class SkillSource(Protocol):
    """Adapters authorize discovery and load an exact reviewed revision.

    Implementations choose storage and parse SKILL.md if appropriate. Resource
    references are opaque; the framework neither reads nor executes them.
    """

    def discover(self, *, scope: str) -> tuple[SkillDescriptor, ...]: ...

    def load(self, skill_id: str, *, version: str) -> Skill: ...

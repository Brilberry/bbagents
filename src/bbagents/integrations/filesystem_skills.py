"""Optional reviewed-manifest Agent Skills loader; never executes bundled code."""

from dataclasses import dataclass, field
import hashlib
from importlib import import_module
from pathlib import Path
from typing import Mapping

from ..revisions import AccessContext, AccessPolicy
from ..skills import Skill, SkillDescriptor


@dataclass(frozen=True, slots=True)
class PinnedSkill:
    """Trusted deployment manifest, kept outside the agent-writable workspace."""

    descriptor: SkillDescriptor
    directory: str
    sha256: str
    resources: Mapping[str, str] = field(default_factory=dict)


class FilesystemSkillSource:
    def __init__(
        self,
        root: Path,
        pins: tuple[PinnedSkill, ...],
        context: AccessContext,
        policy: AccessPolicy,
        *,
        max_bytes: int = 1_000_000,
    ) -> None:
        self.root = root.resolve()
        self.context = context
        self.policy = policy
        self.max_bytes = max_bytes
        self.pins = {(pin.descriptor.id, pin.descriptor.version): pin for pin in pins}
        if len(self.pins) != len(pins):
            raise ValueError("Duplicate skill revision")

    def _authorize(self, operation: str) -> None:
        if not self.policy.permits(self.context, operation):
            raise PermissionError(f"Skill access denied: {operation}")

    def discover(self, *, scope: str) -> tuple[SkillDescriptor, ...]:
        self._authorize("skill.discover")
        if scope != self.context.scope:
            raise PermissionError("Skill scope mismatch")
        return tuple(
            pin.descriptor
            for pin in self.pins.values()
            if pin.descriptor.scope == scope
        )

    def _pin(self, skill_id: str, version: str) -> PinnedSkill:
        pin = self.pins[(skill_id, version)]
        if pin.descriptor.scope != self.context.scope:
            raise PermissionError("Skill scope mismatch")
        return pin

    def _read(self, pin: PinnedSkill, relative: str, digest: str) -> bytes:
        directory = (self.root / pin.directory).resolve()
        path = (directory / relative).resolve()
        if not directory.is_relative_to(self.root) or not path.is_relative_to(
            directory
        ):
            raise PermissionError("Skill path escapes its reviewed directory")
        with path.open("rb") as handle:
            data = handle.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise ValueError("Skill resource exceeds size limit")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Skill revision digest changed")
        return data

    def load(self, skill_id: str, *, version: str) -> Skill:
        self._authorize("skill.load")
        pin = self._pin(skill_id, version)
        text = (
            self._read(pin, "SKILL.md", pin.sha256)
            .decode("utf-8")
            .replace("\r\n", "\n")
        )
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError("SKILL.md requires YAML frontmatter")
        header, instructions = text[4:].split("\n---\n", 1)
        try:
            yaml = import_module("yaml")
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install bbagents[skills] to parse SKILL.md") from exc
        metadata = yaml.safe_load(header)
        if not isinstance(metadata, dict) or metadata.get("name") != skill_id:
            raise ValueError("Skill name does not match manifest")
        if metadata.get("description") != pin.descriptor.description:
            raise ValueError("Skill description does not match manifest")
        return Skill(pin.descriptor, instructions, tuple(pin.resources))

    def read_resource(self, skill_id: str, *, version: str, resource: str) -> bytes:
        self._authorize("skill.resource")
        pin = self._pin(skill_id, version)
        return self._read(pin, resource, pin.resources[resource])

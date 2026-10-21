from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from bbagents.governed_memory import ScopedMemoryStore
from bbagents.integrations.filesystem_skills import FilesystemSkillSource, PinnedSkill
from bbagents.learning import Procedure
from bbagents.memory import MemoryRecord
from bbagents.persistence import SQLiteStateStore
from bbagents.promotion import ProcedurePromoter, PromotionDecision
from bbagents.provenance import Provenance
from bbagents.revisions import AccessContext, RevisionRepository
from bbagents.skills import SkillDescriptor


class Policy:
    def permits(self, context, operation):
        return context.subject_id == "maintainer"


def repository(path, scope="a", subject="maintainer", namespace="memory"):
    return RevisionRepository(
        SQLiteStateStore(path),
        AccessContext(subject, scope),
        Policy(),
        namespace=namespace,
    )


def test_memory_scope_filtering_precedes_embeddings_and_rollback_keeps_history(
    tmp_path,
):
    path = tmp_path / "memory.db"
    a = ScopedMemoryStore(repository(path))
    first = a.remember(MemoryRecord("old", id="one"))
    second = a.remember(replace(first, content="new"), expected_version=1)
    assert a.get("one").content == "new"
    assert ScopedMemoryStore(repository(path, "b")).retrieve("new") == []
    embed_calls = []
    denied = ScopedMemoryStore(
        repository(path, subject="intruder"),
        embed=lambda text: embed_calls.append(text),
    )
    with pytest.raises(PermissionError):
        denied.retrieve("new")
    assert embed_calls == []
    a.revisions.activate("one", 1, expected_active=second.version, reason="rollback")
    assert a.get("one").content == "old"
    assert len(a.revisions.history("one")) == 2
    assert a.revisions.audit("one")[-1]["reason"] == "rollback"


def test_failed_procedure_promotion_keeps_active_and_rollback_is_audited(tmp_path):
    repo = repository(tmp_path / "procedures.db", namespace="procedures")
    promoter = ProcedurePromoter(
        repo,
        lambda p: PromotionDecision(p.value != "bad", "eval", ("suite:1",)),
        encode=str,
    )
    procedure = Procedure("checks", "good", scope="a")
    promoter.promote(procedure, expected_revision=0)
    promoter.promote(replace(procedure, value="bad", version="2"), expected_revision=1)
    assert repo.current("checks").number == 1
    promoter.promote(
        replace(procedure, value="better", version="3"), expected_revision=2
    )
    promoter.rollback("checks", 1, expected_active=3, reason="regression")
    assert repo.current("checks").value["value"] == "good"
    with pytest.raises(ValueError, match="unapproved"):
        promoter.rollback("checks", 2, expected_active=1, reason="unsafe")
    assert len(repo.history("checks")) == 3


def test_skill_manifest_discovery_pinning_and_resource_authority(tmp_path: Path):
    pytest.importorskip("yaml")
    directory = tmp_path / "release"
    directory.mkdir()
    content = b"---\nname: release\ndescription: Release guidance\n---\nValidate before publishing.\n"
    (directory / "SKILL.md").write_bytes(content)
    descriptor = SkillDescriptor(
        "release",
        "1",
        "maintainers",
        "a",
        "Release guidance",
        Provenance.external("reviewed"),
    )
    pin = PinnedSkill(descriptor, "release", hashlib.sha256(content).hexdigest())
    source = FilesystemSkillSource(
        tmp_path, (pin,), AccessContext("maintainer", "a"), Policy()
    )
    assert source.discover(scope="a") == (descriptor,)
    assert "Validate" in source.load("release", version="1").instructions
    (directory / "SKILL.md").write_text("changed")
    assert source.discover(scope="a") == (descriptor,)
    with pytest.raises(ValueError, match="digest"):
        source.load("release", version="1")
    with pytest.raises(PermissionError):
        source.discover(scope="b")
    outside = FilesystemSkillSource(
        tmp_path,
        (replace(pin, directory="../outside"),),
        AccessContext("maintainer", "a"),
        Policy(),
    )
    with pytest.raises(PermissionError, match="escapes"):
        outside.load("release", version="1")


def test_skill_resource_loading_does_not_execute_scripts(tmp_path):
    directory = tmp_path / "release"
    directory.mkdir()
    payload = b"raise RuntimeError('must never execute')"
    (directory / "check.py").write_bytes(payload)
    descriptor = SkillDescriptor(
        "release", "1", "owner", "a", "guide", Provenance.external("reviewed")
    )
    pin = PinnedSkill(
        descriptor,
        "release",
        "unused",
        {"check.py": hashlib.sha256(payload).hexdigest()},
    )
    source = FilesystemSkillSource(
        tmp_path, (pin,), AccessContext("maintainer", "a"), Policy()
    )
    assert source.read_resource("release", version="1", resource="check.py") == payload
    with pytest.raises(KeyError):
        source.read_resource("release", version="1", resource="../secret")

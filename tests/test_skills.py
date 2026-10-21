from bbagents import Action, ActionRuntime
from bbagents.provenance import Provenance
from bbagents.safety import CapabilityPolicy
from bbagents.skills import Skill, SkillDescriptor


def test_loaded_skill_retains_revision_and_does_not_grant_capability() -> None:
    descriptor = SkillDescriptor(
        "release",
        "2",
        "maintainers",
        "repository:bbagents",
        "Release guidance",
        Provenance.external("reviewed-repository"),
        required_tools=("publish",),
    )
    skill = Skill(descriptor, "Validate then publish.", ("checks:version-2",))
    item = skill.context_item()
    assert item.provenance is descriptor.provenance
    assert item.metadata["skill_version"] == "2"
    assert item.metadata["resource_refs"] == ("checks:version-2",)
    effects: list[str] = []
    runtime = ActionRuntime(
        {"publish": lambda: effects.append("published")}, policy=CapabilityPolicy([])
    )
    assert not runtime.execute(Action("publish")).success
    assert effects == []

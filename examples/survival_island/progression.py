"""Reviewed skill catalog and game-owned prerequisite evaluation."""
from dataclasses import dataclass

from bbagents.provenance import Provenance
from bbagents.skills import Skill, SkillDescriptor


@dataclass(frozen=True)
class SkillRule:
    description: str
    base_age: int
    intelligence_factor: int = 0
    prerequisites: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    signal: str = ""
    tools: tuple[str, ...] = ()

    def age(self, intelligence):
        return max(0, self.base_age - self.intelligence_factor * max(0, min(100, intelligence)))


RULES = {
    "spear": SkillRule("Craft a defensive spear from three wood", 180, 1, resources=("wood",)),
    "axe": SkillRule("Craft a stone axe from three wood and one stone", 350, 2, resources=("wood", "stone")),
    "bow": SkillRule("Craft a bow from five wood and one stone", 400, 2, resources=("wood", "stone"), signal="wildlife"),
    "hunting": SkillRule("Hunt visible wildlife with a bow", 0, prerequisites=("bow",), tools=("bow",)),
    "fishing": SkillRule("Craft a rod and fish in lakes", 700, 5, signal="lake"),
    "gardening": SkillRule("Plant wild vegetable seeds with berry compost, wood and water near home", 450, 2,
                           resources=("wood", "berries"), signal="home"),
    "fire": SkillRule("Light a fire with three wood and two stone; refuel with wood", 350, 2,
                      resources=("wood", "stone")),
    "cooking": SkillRule("Roast fish or meat at a burning fire for improved nutrition", 450, 2,
                         prerequisites=("fire",), signal="raw_food"),
    "skinning": SkillRule("Skin a nearby unharvested carcass with a stone knife", 600, 3,
                          prerequisites=("hunting",), signal="hunt"),
    "leatherworking": SkillRule("Tan hides using wood and water; craft protective leather clothing", 800, 3,
                               prerequisites=("skinning",), signal="hide"),
    "reservoir": SkillRule("Craft and install a ten-flask shelter water reservoir", 500, 2,
                           resources=("wood", "stone"), signal="home"),
}

RECIPES = {
    "axe": ("axe", {"wood": 3, "stone": 1}),
    "spear": ("spear", {"wood": 3}),
    "bow": ("bow", {"wood": 5, "stone": 1}),
    "fishing_rod": ("fishing", {"wood": 4}),
    "flask": (None, {"wood": 2}),
    "ration": (None, {"berries": 2}),
    "knife": ("hunting", {"wood": 1, "stone": 2}),
    "leather_garment": ("leatherworking", {"leather": 2}),
    "reservoir": ("reservoir", {"wood": 6, "stone": 3}),
}

NUTRITION = {"berries": 10.0, "ration": 20.0, "fish": 50.0, "meat": 35.0,
             "vegetables": 30.0, "cooked_fish": 70.0, "cooked_meat": 55.0}


class IslandSkills:
    """bbagents SkillSource: immutable authored guidance, never a tool permission."""
    scope = "survival_island"

    def discover(self, *, scope):
        if scope != self.scope:
            return ()
        return tuple(self.load(identity, version="1").descriptor for identity in RULES)

    def load(self, skill_id, *, version):
        if skill_id not in RULES or version != "1":
            raise ValueError("unknown skill or unsupported revision")
        rule = RULES[skill_id]
        descriptor = SkillDescriptor(skill_id, "1", "island", self.scope,
            rule.description, Provenance.internal("reviewed-island-skills"),
            required_tools={"gardening": ("plant", "harvest"), "fire": ("light_fire",),
                "cooking": ("cook",), "skinning": ("skin",), "leatherworking": ("tan_hide", "craft", "equip"),
                "reservoir": ("craft", "install_reservoir", "fill_reservoir", "drink_reservoir")}.get(skill_id, ("craft",)))
        return Skill(descriptor, rule.description + ". Prerequisite knowledge: " +
            (", ".join(rule.prerequisites) or "none") + ". Learning does not bypass tool validation.")


def missing_prerequisites(agent, skill):
    return [name for name in RULES[skill].prerequisites if name not in agent.learned_skills]


def ready(sim, agent, identity):
    rule = RULES[identity]
    if (not agent.alive or identity in agent.learned_skills or missing_prerequisites(agent, identity)
            or sim.tick - agent.born_tick < rule.age(agent.intelligence)
            or any(not agent.known_resources.get(name) for name in rule.resources)
            or any(not agent.inventory[name] for name in rule.tools)):
        return False
    return (not rule.signal or
        (rule.signal == "wildlife" and agent.seen_wildlife) or
        (rule.signal == "lake" and any(tile.value == "lake" for tile in agent.known_tiles.values())) or
        (rule.signal == "home" and sim.home_is_valid(agent)) or
        (rule.signal == "raw_food" and any(agent.inventory[name] for name in ("fish", "meat"))) or
        (rule.signal == "hide" and agent.inventory["hide"] > 0) or
        (rule.signal == "hunt" and agent.hunting_kills > 0))


def catalog(sim, agent):
    return [{"id": identity, "description": rule.description,
             "learned": identity in agent.learned_skills,
             "minimum_discovery_age": rule.age(agent.intelligence),
             "missing_prerequisites": missing_prerequisites(agent, identity),
             "required_discoveries": list(rule.resources), "discovery_signal": rule.signal,
             "required_tools_for_discovery": list(rule.tools)} for identity, rule in RULES.items()]

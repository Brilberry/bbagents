"""Validated data-only teams and reproducible, headless competition runs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

GOALS = ["escape_danger", "secure_water", "find_food", "recover",
         "establish_shelter", "learn_skills", "help_others", "explore"]
ATTRIBUTES = {"intelligence", "endurance", "strength", "perception"}
PERSONALITY = {"generosity", "cooperation", "caution"}
PREFERENCES = {"shelter": ("solo", "shared", "either"),
               "combat": ("avoid", "defend"),
               "knowledge": ("share", "trade", "private"),
               "food": ("berries", "fish", "adaptive")}
DEFAULT_BEHAVIOR = {"priorities": GOALS, "thresholds": {"hunger": 6, "thirst": 5, "energy": 4},
    "preferences": {"shelter": "either", "combat": "avoid", "knowledge": "share", "food": "adaptive"},
    "trading": {"enabled": True, "reserve": 2}}
MAPS = {"easy-island": {"resources": 2, "regrowth": 4, "predator_interval": 500},
        "balanced-island": {"resources": 1, "regrowth": 2, "predator_interval": 250},
        "hard-island": {"resources": 0.5, "regrowth": 1, "predator_interval": 125}}


def obj(value, keys, path, required=()):
    if not isinstance(value, dict) or not set(required) <= set(value) <= set(keys):
        raise ValueError(f"{path}: expected object with keys {sorted(keys)}; required {sorted(required)}")


def integer(value, low, high, path):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{path}: expected integer {low}–{high}")


def label(value, maximum, path):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{path}: expected nonempty text of at most {maximum} characters")


def validate_competition(value):
    keys = {"schema_version", "ruleset_version", "map_name", "team_points", "survivor_count",
            "max_ticks", "seeds", "decision_mode", "scoring", "starting_season"}
    obj(value, keys, "competition", keys - {"starting_season"})
    from .climate import SEASONS
    if not isinstance(value.get("starting_season", "spring"), str) or value.get("starting_season", "spring") not in SEASONS:
        raise ValueError(f"competition.starting_season: expected one of {SEASONS}")
    for key, expected in {"schema_version": 1, "ruleset_version": "island-v1",
                          "decision_mode": "rules", "scoring": "team-survival-v1"}.items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            raise ValueError(f"competition.{key}: expected {expected!r}")
    if not isinstance(value["map_name"], str) or value["map_name"] not in MAPS:
        raise ValueError(f"competition.map_name: expected one of {list(MAPS)}")
    integer(value["survivor_count"], 1, 20, "competition.survivor_count")
    integer(value["team_points"], 4 * value["survivor_count"], 40 * value["survivor_count"], "competition.team_points")
    integer(value["max_ticks"], 1, 1000000, "competition.max_ticks")
    seeds = value["seeds"]
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 100:
        raise ValueError("competition.seeds: expected 1–100 seeds")
    for seed in seeds:
        integer(seed, 0, 4294967295, "competition.seeds[]")
    if len(set(seeds)) != len(seeds):
        raise ValueError("competition.seeds: duplicates are not allowed")
    return {"starting_season": "spring", **deepcopy(value)}


def validate_team(value, competition):
    competition = validate_competition(competition)
    obj(value, {"schema_version", "team_name", "survivors"}, "team", {"schema_version", "team_name", "survivors"})
    integer(value["schema_version"], 1, 1, "schema_version")
    label(value["team_name"], 48, "team_name")
    rows = value["survivors"]
    if not isinstance(rows, list) or len(rows) != competition["survivor_count"]:
        raise ValueError("survivors: count must match competition.survivor_count")
    result = deepcopy(value)
    identities = set()
    points = 0
    for index, row in enumerate(result["survivors"]):
        path = f"survivors[{index}]"
        obj(row, {"id", "name", "attributes", "personality", "behavior"}, path,
            {"id", "name", "attributes", "personality"})
        if not isinstance(row["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", row["id"]) or row["id"] in identities:
            raise ValueError(f"{path}.id: expected unique lowercase identifier, 1–24 characters, starting with a letter")
        identities.add(row["id"])
        label(row["name"], 24, path + ".name")
        for section, keys in (("attributes", ATTRIBUTES), ("personality", PERSONALITY)):
            obj(row[section], keys, path + "." + section, keys)
            for key, number in row[section].items():
                integer(number, 1, 10, f"{path}.{section}.{key}")
        points += sum(row["attributes"].values())
        supplied = row.get("behavior", {})
        obj(supplied, DEFAULT_BEHAVIOR, path + ".behavior")
        behavior = deepcopy(DEFAULT_BEHAVIOR)
        for key, content in supplied.items():
            if key == "priorities":
                if not isinstance(content, list) or len(content) != len(GOALS) or any(not isinstance(x, str) for x in content) or set(content) != set(GOALS):
                    raise ValueError(f"{path}.behavior.priorities: expected each supported goal exactly once")
                behavior[key] = content
                continue
            obj(content, behavior[key], f"{path}.behavior.{key}")
            for name, setting in content.items():
                field = f"{path}.behavior.{key}.{name}"
                if key == "preferences":
                    if not isinstance(setting, str) or setting not in PREFERENCES[name]:
                        raise ValueError(f"{field}: expected one of {PREFERENCES[name]}")
                elif name == "enabled":
                    if type(setting) is not bool:
                        raise ValueError(f"{field}: expected boolean")
                else:
                    integer(setting, 1, 10, field)
                behavior[key][name] = setting
        row["behavior"] = behavior
    if points != competition["team_points"]:
        raise ValueError(f"attributes: spent {points}; expected {competition['team_points']} team points")
    return result


def load(path, *, json5=False):
    source = Path(path)
    if source.stat().st_size > 65536:
        raise ValueError("configuration exceeds 64 KiB")
    text = source.read_text()
    if json5:
        try:
            import json5 as parser
        except ImportError as exc:
            raise ValueError("Install JSON5 support with pip install 'bbagents[island]'") from exc
        return parser.loads(text, allow_duplicate_keys=False)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def create_simulation(team, competition, seed):
    from .simulation import Simulation
    competition = validate_competition(competition)
    team = validate_team(team, competition)
    if seed not in competition["seeds"]:
        raise ValueError("seed must belong to competition.seeds")
    preset = MAPS[competition["map_name"]]
    sim = Simulation(agents=len(team["survivors"]), seed=seed,
                     predator_interval=preset["predator_interval"], team=team,
                     resource_scale=preset["resources"], berry_regrowth=preset["regrowth"],
                     max_ticks=competition["max_ticks"], starting_season=competition["starting_season"])
    return sim


def score(sim):
    lifetimes = [min(agent.death_tick if agent.death_tick is not None else sim.tick,
                     sim.tick) for agent in sim.agents]
    together = min(lifetimes)
    return {"survival_ticks": lifetimes, "all_alive_ticks": together,
            "score": sum(lifetimes) + 5 * together}


def compete(team, competition):
    competition = validate_competition(competition)
    team = validate_team(team, competition)
    runs = []
    for seed in competition["seeds"]:
        sim = create_simulation(team, competition, seed)
        from collections import deque
        sim.trace.events = deque(sim.trace.events, maxlen=30000)
        while not sim.game_over:
            sim.step()
        runs.append({"seed": seed, **score(sim)})
    from importlib.metadata import version
    engine_digest = hashlib.sha256()
    for directory in (Path(__file__).parent, Path(__file__).parents[2] / "src/bbagents"):
        for source in sorted(directory.rglob("*.py")):
            engine_digest.update(str(source.relative_to(directory)).encode())
            engine_digest.update(source.read_bytes())
    return {"engine_version": version("bbagents"), "ruleset_version": competition["ruleset_version"],
            "engine_sha256": engine_digest.hexdigest(),
            "team": team, "competition": competition,
            "configuration_sha256": hashlib.sha256(json.dumps({"team": team, "competition": competition}, sort_keys=True).encode()).hexdigest(),
            "runs": runs, "mean_score": sum(run["score"] for run in runs) / len(runs)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True)
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(compete(load(args.team, json5=True), load(args.competition)), indent=2))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

import pytest

from examples.survival_island import Simulation
from examples.survival_island.climate import Conditions
from examples.survival_island.progression import IslandSkills, RULES, ready
from examples.survival_island.simulation import Terrain


def scene(count=1):
    sim = Simulation(agents=count, predator_interval=None, lightning_probability=0)
    actor = sim.agents[0]
    actor.position = actor.home = (10, 10)
    for pos in sim.world.positions_within(actor.position, 2):
        sim.world.tile(pos).terrain = Terrain.GRASS
    tile = sim.world.tile(actor.home)
    tile.shelter_owner = actor.id
    tile.shelter_residents = {actor.id}
    sim.conditions = Conditions("spring", "sunny", 20)
    return sim, actor


def test_optional_garden_preparation_collects_missing_materials():
    sim, actor = scene()
    sim.learn_skill(actor, "gardening", "teacher")
    actor.inventory.update(berries=4, water=1)
    actor.known_resources["wood"] = {(11, 10)}
    sim.world.tile((11, 10)).resources["wood"] = 5
    call = sim.policy._homestead_action(actor)
    assert call is not None
    assert call.tool_name == "move"


def test_cooking_preparation_collects_fire_materials():
    sim, actor = scene()
    sim.learn_skill(actor, "fire", "teacher")
    sim.learn_skill(actor, "cooking", "teacher")
    actor.inventory["fish"] = 1
    actor.known_resources["wood"] = {(11, 10)}
    sim.world.tile((11, 10)).resources["wood"] = 5
    call = sim.policy._homestead_action(actor)
    assert call is not None
    assert call.tool_name == "move"


def test_skill_catalog_uses_framework_guidance_and_prerequisites():
    source = IslandSkills()
    assert {descriptor.id for descriptor in source.discover(scope="survival_island")} == set(RULES)
    assert not source.discover(scope="another-world")
    assert source.load("gardening", version="1").context_item().source == "gardening"
    sim, actor = scene()
    with pytest.raises(ValueError, match="prerequisite"):
        sim.learn_skill(actor, "hunting", "teacher")
    sim.learn_skill(actor, "bow", "teacher")
    sim.learn_skill(actor, "hunting", "teacher")
    assert "hunting" in actor.learned_skills


def test_gardening_discovery_and_finite_cost_growth_harvest():
    sim, actor = scene()
    actor.known_resources.update(wood={(1, 1)}, berries={(2, 1)})
    actor.intelligence = 100
    sim.tick = 249
    assert not ready(sim, actor, "gardening")
    sim.tick = 250
    assert ready(sim, actor, "gardening")
    sim.discover_skills(actor)
    actor.position = (11, 10)
    actor.inventory.update(berries=4, wood=2, water=1)
    sim.homestead.execute(actor, "plant")
    assert actor.inventory["water"] == 0 and actor.inventory["flask"] == 1
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "harvest")
    for _ in range(160):
        sim.homestead.update()
    sim.homestead.execute(actor, "harvest")
    assert actor.inventory["vegetables"] == 6
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "harvest")


def test_garden_growth_weather_and_access_control():
    sim, actor = scene(count=2)
    sim.learn_skill(actor, "gardening", "teacher")
    actor.position = (11, 10)
    actor.inventory.update(berries=2, wood=1, water=1)
    sim.homestead.execute(actor, "plant")
    plot = sim.homestead.plots[actor.position]
    sim.conditions = Conditions("winter", "sunny", 2)
    sim.homestead.update()
    assert plot.progress == 0
    sim.conditions = Conditions("spring", "rainy", 15)
    sim.homestead.update()
    assert plot.progress == 1.5
    plot.progress = 160
    outsider = sim.agents[1]
    outsider.position = actor.position
    with pytest.raises(ValueError):
        sim.homestead.execute(outsider, "harvest")
    sim.conditions = Conditions("winter", "snowy", -10)
    sim.homestead.update()
    assert not sim.homestead.plots


def test_fire_fuel_cooking_and_expiry():
    sim, actor = scene()
    sim.learn_skill(actor, "fire", "teacher")
    sim.learn_skill(actor, "cooking", "teacher")
    actor.inventory.update(wood=4, stone=2, fish=1)
    sim.homestead.execute(actor, "light_fire")
    assert sim.homestead.warmth(actor.position) == 8
    sim.homestead.execute(actor, "cook", item="fish")
    assert actor.inventory["fish"] == 0 and actor.inventory["cooked_fish"] == 1
    actor.hunger = 90
    sim.eat(actor, "cooked_fish")
    assert actor.hunger == 20
    sim.homestead.execute(actor, "light_fire")
    assert sim.homestead.fires[actor.position].fuel == 120
    for _ in range(120):
        sim.homestead.update()
    assert not sim.homestead.fires
    actor.inventory["meat"] = 1
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "cook", item="meat")


def test_fire_reduces_exposure_and_outdoor_rain_consumes_fuel():
    sim, actor = scene()
    sim.learn_skill(actor, "fire", "teacher")
    actor.inventory.update(wood=3, stone=2)
    sim.homestead.execute(actor, "light_fire")
    sim.conditions = Conditions("winter", "sunny", -15)
    actor.cold_exposure = 20
    sim._apply_needs(actor)
    assert actor.cold_exposure < 20
    sim.world.tile(actor.position).shelter_owner = None
    sim.conditions = Conditions("autumn", "rainy", 5)
    sim.homestead.update()
    assert sim.homestead.fires[actor.position].fuel == 78


def test_skinning_once_tanning_crafting_and_equipping():
    from examples.survival_island.wildlife import Animal
    sim, actor = scene()
    for skill in ("bow", "hunting", "skinning", "leatherworking"):
        sim.learn_skill(actor, skill, "teacher")
    actor.inventory.update(wood=5, stone=2, water=2)
    sim.craft(actor, "knife")
    corpse = Animal("deer", "deer", actor.position, 0)
    sim.wildlife.animals = [corpse]
    sim.homestead.execute(actor, "skin", animal_id=corpse.id)
    assert actor.inventory["hide"] == 2
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "skin", animal_id=corpse.id)
    for _ in range(2):
        sim.homestead.execute(actor, "tan_hide")
    sim.craft(actor, "leather_garment")
    sim.homestead.execute(actor, "equip", item="leather_garment")
    assert actor.inventory["leather"] == 0 and actor.leather_equipped
    assert actor.inventory["flask"] == 2


def test_leather_reduces_cold_but_does_not_provide_immunity():
    sim, actor = scene()
    sim.world.tile(actor.position).shelter_owner = None
    sim.conditions = Conditions("winter", "snowy", -10)
    actor.leather_equipped = True
    actor.inventory["leather_garment"] = 1
    sim._apply_needs(actor)
    clothed = actor.cold_exposure
    assert clothed > 0
    actor.cold_exposure = 0
    actor.inventory["leather_garment"] = 0
    sim._apply_needs(actor)
    assert actor.cold_exposure > clothed


def test_reservoir_capacity_no_free_water_and_drinking():
    sim, actor = scene()
    sim.learn_skill(actor, "reservoir", "teacher")
    actor.inventory.update(wood=6, stone=3, water=11)
    sim.craft(actor, "reservoir")
    sim.homestead.execute(actor, "install_reservoir")
    reservoir = sim.homestead.reservoirs[actor.home]
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "drink_reservoir")
    for _ in range(10):
        sim.homestead.execute(actor, "fill_reservoir")
    assert reservoir.water == reservoir.capacity == 10
    assert actor.inventory["water"] == 1 and actor.inventory["flask"] == 10
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "fill_reservoir")
    assert actor.inventory["water"] == 1
    actor.thirst = 80
    sim.drink(actor)
    assert reservoir.water == 9 and actor.thirst == 30


def test_reservoir_shared_access_inheritance_and_lightning():
    sim, owner = scene(count=2)
    heir = sim.agents[1]
    home = owner.home
    sim.learn_skill(owner, "reservoir", "teacher")
    owner.inventory["reservoir"] = 1
    sim.homestead.execute(owner, "install_reservoir")
    reservoir = sim.homestead.reservoirs[home]
    reservoir.water = 4
    heir.position = home
    with pytest.raises(ValueError):
        sim.homestead.execute(heir, "drink_reservoir")
    owner.alive = False
    sim.settle_estates()
    sim.claim_shelter(heir)
    heir.thirst = 80
    sim.homestead.execute(heir, "drink_reservoir")
    assert reservoir.water == 3
    sim.lightning_strike(home)
    assert home not in sim.homestead.reservoirs


def test_urgent_water_plan_uses_reservoir_and_optional_policy_refills():
    from bbagents import PlanStep
    sim, actor = scene()
    sim.learn_skill(actor, "reservoir", "teacher")
    actor.inventory["reservoir"] = 1
    sim.homestead.execute(actor, "install_reservoir")
    reservoir = sim.homestead.reservoirs[actor.home]
    reservoir.water = 2
    actor.thirst = 80
    call, done = sim.policy._call_for_step(actor, PlanStep("drink", "Drink"))
    assert call.tool_name == "drink_reservoir" and not done
    sim.tools[actor.id].execute(call)
    actor.inventory["water"] = 1
    assert sim.policy._reservoir_action(actor).tool_name == "fill_reservoir"


def test_homestead_runtime_cannot_spoof_actor_or_run_while_asleep():
    sim, actor = scene()
    with pytest.raises(ValueError, match="arguments"):
        sim.homestead.execute(actor, "plant", actor="another-survivor")
    sim.learn_skill(actor, "fire", "teacher")
    actor.inventory.update(wood=3, stone=2)
    sim.sleep(actor)
    with pytest.raises(ValueError):
        sim.homestead.execute(actor, "light_fire")
    assert actor.inventory["wood"] == 3


def test_autonomous_upgrade_actions_skin_cook_and_fill():
    from examples.survival_island.wildlife import Animal
    sim, actor = scene()
    for skill in ("bow", "hunting", "skinning", "fire", "cooking", "reservoir"):
        sim.learn_skill(actor, skill, "teacher")
    actor.inventory.update(knife=1, fish=1, wood=10, stone=5)
    sim.wildlife.animals = [Animal("rabbit", "rabbit", actor.position, 0)]
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "skin"
    sim.tools[actor.id].execute(call)
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "light_fire"
    sim.tools[actor.id].execute(call)
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "cook"
    sim.tools[actor.id].execute(call)
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "craft" and call.arguments["item"] == "reservoir"
    sim.tools[actor.id].execute(call)
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "install_reservoir"
    sim.tools[actor.id].execute(call)
    actor.inventory["water"] = 1
    call = sim.policy._homestead_action(actor)
    assert call.tool_name == "fill_reservoir"


def test_model_homestead_tasks_use_registered_actions():
    from bbagents import PlanStep
    from examples.survival_island.model_policy import ModelPolicy
    sim, actor = scene()
    for skill in ("gardening", "fire", "cooking", "reservoir"):
        sim.learn_skill(actor, skill, "teacher")
    policy = ModelPolicy(sim, model="fake")
    sim.policy = policy
    try:
        actor.inventory.update(berries=6, wood=20, stone=10, water=2, fish=1)
        actor.position = (11, 10)
        call, _ = policy._task_call(actor, PlanStep("garden", "Garden", description="grow_vegetables"))
        assert call.tool_name == "plant"
        sim.tools[actor.id].execute(call)
        assert policy._task_call(actor, PlanStep("garden", "Garden", description="grow_vegetables"))[1]
        actor.position = actor.home
        call, _ = policy._task_call(actor, PlanStep("fire", "Fire", description="make_fire"))
        assert call.tool_name == "light_fire"
        sim.tools[actor.id].execute(call)
        call, _ = policy._task_call(actor, PlanStep("cook", "Cook", description="cook_food"))
        assert call.tool_name == "cook"
        sim.tools[actor.id].execute(call)
        call, _ = policy._task_call(actor, PlanStep("water", "Water", description="store_water"))
        assert call.tool_name == "craft" and call.arguments["item"] == "reservoir"
    finally:
        policy.close()


def test_completed_upgrades_appear_in_single_markdown_report(tmp_path):
    from examples.survival_island.reporting import write_reports
    sim, actor = scene()
    sim.learn_skill(actor, "reservoir", "teacher")
    actor.inventory.update(reservoir=1, water=1)
    sim.homestead.execute(actor, "install_reservoir")
    sim.homestead.execute(actor, "fill_reservoir")
    write_reports(sim, tmp_path)
    report = (tmp_path / "survival-report.md").read_text()
    assert "home_reservoir" in report and '"water": 1' in report
    assert "reservoir.installed" in report and "reservoir.filled" in report


def test_shared_residents_access_reservoir_but_visitors_do_not():
    sim, owner = scene(count=3)
    resident, visitor = sim.agents[1:]
    resident.home = resident.position = visitor.position = owner.home
    tile = sim.world.tile(owner.home)
    tile.shelter_capacity = 2
    tile.shelter_residents.add(resident.id)
    sim.learn_skill(owner, "reservoir", "teacher")
    owner.inventory["reservoir"] = 1
    sim.homestead.execute(owner, "install_reservoir")
    sim.homestead.reservoirs[owner.home].water = 2
    resident.thirst = visitor.thirst = 80
    sim.homestead.execute(resident, "drink_reservoir")
    assert resident.thirst == 30
    with pytest.raises(ValueError):
        sim.homestead.execute(visitor, "drink_reservoir")

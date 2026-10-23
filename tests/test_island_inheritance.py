from collections import Counter

import pytest

from examples.survival_island import Simulation


def home(sim, residents):
    position = (10, 10)
    tile = sim.world.tile(position)
    tile.shelter_owner = residents[0].id
    tile.shelter_residents = {agent.id for agent in residents}
    tile.shelter_capacity = len(residents)
    for agent in residents:
        agent.home = position
    return position, tile


def test_death_away_from_home_stores_estate_and_claim_inherits_once():
    sim = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    owner, claimant = sim.agents
    position, tile = home(sim, [owner])
    owner.inventory = Counter(wood=7, berries=3, fishing_rod=1)
    owner.position = (1, 1)
    owner.alive = False
    claimant.position = position
    sim.settle_estates()
    sim.settle_estates()
    assert tile.shelter_storage == Counter(wood=7, berries=3, fishing_rod=1)
    assert not owner.inventory
    assert owner.home is None
    assert sim.shelter_is_available(claimant, position)
    observation = sim.claim_shelter(claimant)
    assert observation.metadata["inherited"]["wood"] == 7
    assert claimant.inventory == Counter(wood=7, berries=3, fishing_rod=1)
    assert not tile.shelter_storage
    sim.claim_shelter(claimant)
    assert claimant.inventory["wood"] == 7
    assert "fishing" not in claimant.learned_skills
    assert len(sim.trace.events_named("survivor.estate_stored")) == 1


def test_shared_home_is_not_vacant_until_last_resident_dies():
    sim = Simulation(agents=3, predator_interval=None, lightning_probability=0)
    owner, resident, outsider = sim.agents
    position, tile = home(sim, [owner, resident])
    owner.inventory["berries"] = 4
    owner.alive = False
    sim.settle_estates()
    assert tile.shelter_owner == resident.id
    outsider.position = resident.position = position
    with pytest.raises(ValueError, match="unoccupied"):
        sim.claim_shelter(outsider)
    sim.claim_shelter(resident)
    assert resident.inventory["berries"] == 4
    assert tile.shelter_residents == {resident.id}
    resident.alive = False
    sim.settle_estates()
    sim.claim_shelter(outsider)
    assert outsider.inventory["berries"] == 4
    assert tile.shelter_capacity == 2


def test_lightning_destroys_stored_estate_without_leaking_into_rebuilt_home():
    sim = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    owner, claimant = sim.agents
    position, tile = home(sim, [owner])
    owner.inventory["wood"] = 12
    owner.alive = False
    sim.settle_estates()
    result = sim.lightning_strike(position)
    assert result.metadata["destroyed_storage"] == {"wood": 12}
    assert not tile.shelter_storage
    assert not sim.shelter_is_available(claimant, position)


def test_needs_death_automatically_stores_resources():
    sim = Simulation(agents=2, predator_interval=None, lightning_probability=0)
    owner, _ = sim.agents
    _, tile = home(sim, [owner])
    owner.health, owner.hunger = 1, 95
    owner.inventory["stone"] = 3
    sim.step()
    assert not owner.alive
    assert tile.shelter_storage["stone"] == 3

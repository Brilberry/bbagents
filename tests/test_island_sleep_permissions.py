import pytest

from bbagents import PlanStep
from examples.survival_island import Simulation


def setup():
    sim = Simulation(agents=3, predator_interval=None, lightning_probability=0)
    owner, guest, other = sim.agents
    tile = sim.world.tile(owner.position)
    tile.shelter_owner = owner.id
    tile.shelter_residents = {owner.id}
    owner.home = owner.position
    guest.position = other.position = owner.position
    return sim, owner, guest, other, tile


@pytest.mark.parametrize("away", [False, True])
def test_solo_shelter_is_private_even_when_owner_is_away(away):
    sim, owner, guest, _, _ = setup()
    if away:
        owner.position = (1, 1)
    assert not sim.sleep_site_available(guest, guest.position)
    assert not any(call.tool_name == "sleep" for call in sim.available_actions(guest))
    with pytest.raises(ValueError, match="private"):
        sim.sleep(guest)


def test_shared_shelter_allows_visitors_up_to_capacity():
    sim, owner, guest, other, tile = setup()
    tile.shelter_capacity = 2
    sim.sleep(owner)
    sim.sleep(guest)
    assert not sim.sleep_site_available(other, other.position)
    assert guest.home is None  # Visiting does not change ownership or residence.
    assert tile.shelter_owner == owner.id


def test_abandoned_shared_shelter_requires_claim_before_sleep():
    sim, owner, guest, _, tile = setup()
    tile.shelter_capacity = 2
    owner.alive = False
    assert not sim.sleep_site_available(guest, guest.position)
    call, done = sim.policy._call_for_step(guest, PlanStep("sleep", "Sleep"))
    assert call.tool_name == "claim_shelter" and not done
    sim.tools[guest.id].execute(call)
    sim.sleep(guest)
    assert guest.home == guest.position

from sww.game import Game
from sww.models import Actor, CharacterInventory, ItemInstance
from sww.ui_headless import HeadlessUI


def test_party_encumbrance_bridge_uses_new_actor_inventory_weights():
    g = Game(HeadlessUI(), dice_seed=9001, wilderness_seed=9002)
    a = Actor(name="A", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    a.inventory = CharacterInventory(coins_gp=5000, items=[ItemInstance(instance_id="itm-1", template_id="gear.rock", name="Rock", category="gear", quantity=1)])
    g.party.members = [a]

    enc = g.party_encumbrance()

    assert hasattr(enc, "state")
    assert enc.state in {"heavy", "severe", "overloaded"}

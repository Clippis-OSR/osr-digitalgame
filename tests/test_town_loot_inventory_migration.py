from sww.game import Game
from sww.models import Actor
from sww.ui_headless import HeadlessUI


def _game(seed: int = 4100) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_buying_weapon_puts_item_in_inventory_and_syncs_legacy_on_autoequip():
    g = _game(4200)
    a = Actor(name="Buyer", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    g.party.members = [a]

    ok = g._buy_item_for_actor(a, name="Sword, long", kind="weapon", auto_equip=True)

    assert ok is True
    assert any(getattr(it, "template_id", "") == "weapon.sword_long" for it in (a.inventory.items or []))
    assert a.weapon == "Sword, long"
    assert a.equipment.main_hand == "Sword, long"


def test_assigning_loot_to_character_creates_item_instance():
    g = _game(4300)
    a = Actor(name="Looter", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    g.party.members = [a]

    loot = {"name": "Dagger", "kind": "weapon", "gp_value": 2}
    ok = g._add_loot_item_to_actor_inventory(a, loot)

    assert ok is True
    assert any(getattr(it, "name", "") == "Dagger" for it in (a.inventory.items or []))

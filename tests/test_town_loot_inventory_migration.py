from sww.game import Game
from sww.models import Actor, ItemInstance
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


def test_sell_actor_owned_item_removes_from_inventory_and_awards_gold():
    g = _game(4400)
    a = Actor(name="Seller", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    g.party.members = [a]
    a.ensure_inventory_initialized()
    a.inventory.items.append(
        ItemInstance(
            instance_id="actor-sell-1",
            template_id="weapon.sword_long",
            name="Sword, long",
            category="weapon",
            quantity=1,
            identified=True,
            metadata={"gp_value": 20},
        )
    )

    g.sell_loot()

    assert g.gold == 10
    assert len(a.inventory.items) == 0


def test_sell_stash_item_removes_from_stash_and_awards_gold():
    g = _game(4410)
    g.party.members = []
    g.party_stash.items.append(
        ItemInstance(
            instance_id="stash-sell-1",
            template_id="treasure.generic",
            name="Stash Jewel",
            category="treasure",
            quantity=1,
            identified=False,
            metadata={"value_gp": 40},
        )
    )

    g.sell_loot()

    assert g.gold == 20
    assert len(g.party_stash.items) == 0


def test_sell_legacy_party_item_compatibility_path_still_works_explicitly():
    g = _game(4420)
    g.party.members = []
    g._ingest_reward_items_to_loot_pool([{"name": "Legacy Gem", "kind": "treasure", "gp_value": 50}], source="legacy_test")
    assert len(g.loot_pool.entries) == 1

    ok = g.sell_legacy_party_item(g.loot_pool.entries[0].entry_id)

    assert ok is True
    assert g.gold == 25
    assert len(g.loot_pool.entries) == 0


def test_sell_actor_item_uses_treasury_destination():
    g = _game(4430)
    a = Actor(name="Seller", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    g.party.members = [a]
    a.ensure_inventory_initialized()
    a.inventory.items.append(
        ItemInstance(
            instance_id="actor-sell-2",
            template_id="weapon.dagger",
            name="Dagger",
            category="weapon",
            quantity=1,
            identified=True,
            metadata={"gp_value": 6},
        )
    )

    assert g.sell_actor_item(a, "actor-sell-2") is True
    assert any("to treasury." in ln for ln in g.ui.lines)

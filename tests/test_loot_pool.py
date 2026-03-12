from sww.loot_pool import (
    add_generated_treasure_to_pool,
    assign_loot_to_actor,
    create_loot_pool,
    leave_loot_behind,
    move_loot_to_party_stash,
)
from sww.game import Game
from sww.models import Actor
from sww.ui_headless import HeadlessUI


def test_generated_weapon_enters_loot_pool():
    pool = create_loot_pool()
    gp, added = add_generated_treasure_to_pool(
        pool,
        gp=0,
        items=[{"name": "Sword, long", "kind": "weapon", "gp_value": 15}],
    )
    assert gp == 0
    assert len(added) == 1
    assert added[0].kind == "weapon"
    assert len(pool.entries) == 1


def test_magic_item_enters_loot_pool_unidentified_when_appropriate():
    pool = create_loot_pool()
    _, added = add_generated_treasure_to_pool(
        pool,
        gp=0,
        items=[{"name": "Cloudy Potion", "true_name": "Potion of Healing", "kind": "potion"}],
        identify_magic=False,
    )
    assert len(added) == 1
    assert added[0].identified is False
    assert added[0].item_instance is not None
    assert added[0].item_instance.identified is False


def test_assigning_item_to_actor_puts_it_in_inventory():
    pool = create_loot_pool()
    _, added = add_generated_treasure_to_pool(pool, items=[{"name": "Dagger", "kind": "weapon"}])
    a = Actor(name="Lina", hp=4, hp_max=4, ac_desc=8, hd=1, save=15, is_pc=True)
    ok = assign_loot_to_actor(pool, a, added[0].entry_id)
    assert ok is True
    assert len(a.inventory.items) == 1
    assert len(pool.entries) == 0


def test_leaving_item_behind_does_not_add_inventory():
    pool = create_loot_pool()
    _, added = add_generated_treasure_to_pool(pool, items=[{"name": "Scroll", "kind": "scroll"}])
    a = Actor(name="Mira", hp=4, hp_max=4, ac_desc=8, hd=1, save=15, is_pc=True)
    assert leave_loot_behind(pool, added[0].entry_id) is True
    assert len(pool.entries) == 0
    assert len(a.inventory.items) == 0


def test_move_item_to_party_stash_removes_from_active_pool():
    pool = create_loot_pool()
    _, added = add_generated_treasure_to_pool(pool, items=[{"name": "Silver Idol", "kind": "treasure", "gp_value": 100}])
    assert move_loot_to_party_stash(pool, added[0].entry_id) is True
    assert len(pool.entries) == 0
    assert len(pool.stash) == 1


def test_room_treasure_path_adds_items_to_loot_pool():
    g = Game(HeadlessUI(), dice_seed=30100, wilderness_seed=30101)
    room = {"id": 1, "treasure_taken": False, "treasure_gp": 12, "treasure_items": [{"name": "Dagger", "kind": "weapon"}], "_delta": {}}

    g._handle_room_treasure(room)

    assert g.gold == 12
    assert len(g.loot_pool.entries) == 1
    assert len(g.party_items) == 1
    assert room["treasure_taken"] is True

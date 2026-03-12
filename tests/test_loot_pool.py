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



def test_room_treasure_uses_coin_reward_service_once(monkeypatch):
    import sww.reward_bundle as reward_bundle_mod

    g = Game(HeadlessUI(), dice_seed=30110, wilderness_seed=30111)
    room = {
        "id": 1,
        "treasure_taken": False,
        "treasure_gp": 12,
        "treasure_items": [{"name": "Dagger", "kind": "weapon"}],
        "_delta": {},
    }

    calls = []
    real = reward_bundle_mod.grant_coin_reward

    def _spy(game, amount_gp, **kwargs):
        calls.append((int(amount_gp), dict(kwargs)))
        return real(game, amount_gp, **kwargs)

    monkeypatch.setattr(reward_bundle_mod, "grant_coin_reward", _spy)

    g._handle_room_treasure(room)

    assert g.gold == 12
    assert len(calls) == 1
    assert calls[0][0] == 12
    assert calls[0][1].get("source") == "room_treasure"
    assert len(g.loot_pool.entries) == 1
    assert len(g.party_items) == 1


def test_boss_reward_uses_coin_reward_service_without_double_credit(monkeypatch):
    import sww.reward_bundle as reward_bundle_mod

    g = Game(HeadlessUI(), dice_seed=30120, wilderness_seed=30121)
    room = {
        "id": 1,
        "type": "boss",
        "cleared": False,
        "boss_loot_taken": False,
        "treasure_gp": 30,
        "treasure_items": [{"name": "Jeweled Idol", "kind": "treasure", "gp_value": 60}],
        "_delta": {},
    }

    calls = []
    real = reward_bundle_mod.grant_coin_reward

    def _spy(game, amount_gp, **kwargs):
        calls.append((int(amount_gp), dict(kwargs)))
        return real(game, amount_gp, **kwargs)

    monkeypatch.setattr(reward_bundle_mod, "grant_coin_reward", _spy)

    g.current_room_id = 1
    g._ensure_room = lambda rid: room
    g._apply_turn_cost = lambda: None
    g._check_wandering_monster = lambda: False
    g._handle_room_specials = lambda r: None
    g._handle_room_monster = lambda r: r.update({"cleared": True})

    res = g._cmd_dungeon_advance_turn()

    assert res.ok is True
    assert g.gold == 30
    assert len(calls) == 1
    assert calls[0][0] == 30
    assert calls[0][1].get("source") == "boss_spoils"
    assert len(g.loot_pool.entries) == 1
    assert room.get("boss_loot_taken") is True


def test_room_treasure_taken_guard_prevents_double_coin_credit(monkeypatch):
    import sww.reward_bundle as reward_bundle_mod

    g = Game(HeadlessUI(), dice_seed=30130, wilderness_seed=30131)
    room = {
        "id": 2,
        "treasure_taken": False,
        "treasure_gp": 14,
        "treasure_items": [{"name": "Mace", "kind": "weapon"}],
        "_delta": {},
    }

    calls = []
    real = reward_bundle_mod.grant_coin_reward

    def _spy(game, amount_gp, **kwargs):
        calls.append((int(amount_gp), dict(kwargs)))
        return real(game, amount_gp, **kwargs)

    monkeypatch.setattr(reward_bundle_mod, "grant_coin_reward", _spy)

    g._handle_room_treasure(room)
    g._handle_room_treasure(room)

    assert g.gold == 14
    assert len(calls) == 1


def test_reward_intake_abandoned_camp_style_writes_loot_pool_first():
    g = Game(HeadlessUI(), dice_seed=30140, wilderness_seed=30141)

    added = g._ingest_reward_items_to_loot_pool(
        [{"name": "Camp Dagger", "kind": "weapon", "gp_value": 2}],
        source="wilderness_abandoned_camp",
    )

    assert len(added) == 1
    assert len(g.loot_pool.entries) == 1
    # Compatibility mirror still present, but source of truth is loot_pool.
    assert len(g.party_items) == 1


def test_sell_loot_still_works_for_loot_pool_reward_intake():
    g = Game(HeadlessUI(), dice_seed=30150, wilderness_seed=30151)
    g._ingest_reward_items_to_loot_pool(
        [{"name": "Camp Jewel", "kind": "treasure", "gp_value": 50}],
        source="wilderness_abandoned_camp",
    )

    g.sell_loot()

    assert g.gold == 25
    assert len(g.loot_pool.entries) == 0

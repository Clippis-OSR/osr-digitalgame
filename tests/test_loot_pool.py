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
    # Migrated room-treasure path should not inject directly into legacy shared loot.
    assert len(g.party_items) == 0
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
    # Room treasure now stays in pending loot until explicit assignment.
    assert len(g.party_items) == 0


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


def test_room_reward_summary_shows_coin_destination_and_items_found():
    g = Game(HeadlessUI(), dice_seed=30160, wilderness_seed=30161)

    g._grant_room_loot(gp=12, items=[{"name": "Room Blade", "kind": "weapon"}], source="room_treasure")

    assert any("Coins gained: 12 gp -> treasury." in ln for ln in g.ui.lines)
    assert any("Items found: 1." in ln for ln in g.ui.lines)


def test_boss_reward_summary_shows_coin_destination_and_items_found():
    g = Game(HeadlessUI(), dice_seed=30170, wilderness_seed=30171)

    g._grant_room_loot(gp=30, items=[{"name": "Boss Idol", "kind": "treasure", "gp_value": 60}], source="boss_spoils")

    assert any("Coins gained: 30 gp -> treasury." in ln for ln in g.ui.lines)
    assert any("Items found: 1." in ln for ln in g.ui.lines)


def test_sale_summary_shows_actor_owned_source_and_coin_result():
    g = Game(HeadlessUI(), dice_seed=30180, wilderness_seed=30181)
    add_generated_treasure_to_pool(
        g.loot_pool,
        gp=0,
        items=[{"name": "Actor Sword", "kind": "weapon", "gp_value": 20, "metadata": {"owner_actor": "Lina"}}],
    )

    g.sell_loot()

    assert any("Sold Actor Sword (actor:Lina) for 10 gp to treasury." in ln for ln in g.ui.lines)


def test_sale_summary_shows_stash_source_and_coin_result():
    g = Game(HeadlessUI(), dice_seed=30190, wilderness_seed=30191)
    add_generated_treasure_to_pool(
        g.loot_pool,
        gp=0,
        items=[{"name": "Stash Gem", "kind": "treasure", "gp_value": 40, "metadata": {"source": "stash"}}],
    )

    g.sell_loot()

    assert any("Sold Stash Gem (stash) for 20 gp to treasury." in ln for ln in g.ui.lines)


def test_sell_loot_uses_centralized_coin_reward_service(monkeypatch):
    import sww.game as game_mod

    g = Game(HeadlessUI(), dice_seed=30220, wilderness_seed=30221)
    add_generated_treasure_to_pool(
        g.loot_pool,
        gp=0,
        items=[{"name": "Service Gem", "kind": "treasure", "gp_value": 30}],
    )

    calls = []
    real = game_mod.grant_coin_reward

    def _spy(game, amount_gp, **kwargs):
        calls.append((int(amount_gp), dict(kwargs)))
        return real(game, amount_gp, **kwargs)

    monkeypatch.setattr(game_mod, "grant_coin_reward", _spy)

    g.sell_loot()

    assert len(calls) == 1
    assert calls[0][0] == 15
    assert calls[0][1].get("source") == "sell_loot"


def test_room_treasure_pending_item_can_be_assigned_to_specific_actor_inventory():
    g = Game(HeadlessUI(), dice_seed=30230, wilderness_seed=30231)
    actor = Actor(name="Nora", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)

    g._grant_room_loot(gp=0, items=[{"name": "Room Spear", "kind": "weapon", "gp_value": 3}], source="room_treasure")

    assert len(g.loot_pool.entries) == 1
    # Regression check: migrated room-treasure intake does not auto-populate shared legacy loot.
    assert len(g.party_items) == 0

    entry = g.loot_pool.entries[0]
    assert assign_loot_to_actor(g.loot_pool, actor, entry.entry_id) is True
    assert len(actor.inventory.items) == 1
    assert str(actor.inventory.items[0].name) == "Room Spear"
    assert len(g.loot_pool.entries) == 0


def test_unidentified_loot_assignment_to_actor_preserves_identified_state():
    pool = create_loot_pool()
    _, added = add_generated_treasure_to_pool(
        pool,
        items=[{"name": "Cloudy Potion", "true_name": "Potion of Healing", "kind": "potion"}],
        identify_magic=False,
    )
    a = Actor(name="Vera", hp=4, hp_max=4, ac_desc=8, hd=1, save=15, is_pc=True)

    ok = assign_loot_to_actor(pool, a, added[0].entry_id)

    assert ok is True
    assert len(a.inventory.items) == 1
    assert a.inventory.items[0].identified is False


def test_room_loot_end_to_end_assignment_menu_to_actor_inventory():
    g = Game(HeadlessUI(), dice_seed=30240, wilderness_seed=30241)
    actor = Actor(name="Rin", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)
    g.party.members = [actor]

    g._grant_room_loot(gp=0, items=[{"name": "Room Knife", "kind": "weapon", "gp_value": 2}], source="room_treasure")
    assert len(g.loot_pool.entries) == 1

    g.assign_party_loot_to_character()

    assert len(g.loot_pool.entries) == 0
    assert any(str(getattr(it, "name", "")) == "Room Knife" for it in (actor.inventory.items or []))


def test_combat_loot_delta_prefers_loot_pool_entries_over_legacy_party_items():
    g = Game(HeadlessUI(), dice_seed=30250, wilderness_seed=30251)
    _gp, added = add_generated_treasure_to_pool(g.loot_pool, items=[{"name": "Old Gem", "kind": "treasure", "gp_value": 10}])
    start_ids = {str(added[0].entry_id)}

    add_generated_treasure_to_pool(g.loot_pool, items=[{"name": "New Gem", "kind": "treasure", "gp_value": 20}])
    # Legacy mirror may be stale/empty; loot-pool delta should still report new item.
    g.party_items = []

    names = g._combat_loot_item_names_delta(start_ids)

    assert "New Gem" in names
    assert "Old Gem" not in names


def _last_encounter_recap_event(game: Game):
    events = [ev for ev in list(getattr(game.events, "events", []) or []) if getattr(ev, "name", "") == "encounter_recap"]
    assert events
    return events[-1]


def test_encounter_end_capture_prefers_loot_pool_delta_when_party_items_stale():
    g = Game(HeadlessUI(), dice_seed=30260, wilderness_seed=30261)
    _gp, added = add_generated_treasure_to_pool(g.loot_pool, items=[{"name": "Old Gem", "kind": "treasure", "gp_value": 10}])
    assert len(added) == 1

    g._encounter_begin_capture(context="test")
    add_generated_treasure_to_pool(g.loot_pool, items=[{"name": "New Gem", "kind": "treasure", "gp_value": 20}])
    # Legacy shared list is intentionally stale/empty; recap should still use loot_pool identity delta.
    g.party_items = []
    g._encounter_end_capture()

    recap = _last_encounter_recap_event(g)
    items = list((recap.data or {}).get("items_gained", []) or [])
    assert "New Gem" in items
    assert "Old Gem" not in items


def test_encounter_end_capture_handles_duplicate_unidentified_labels_by_entry_identity():
    g = Game(HeadlessUI(), dice_seed=30270, wilderness_seed=30271)

    g._encounter_begin_capture(context="test")
    add_generated_treasure_to_pool(
        g.loot_pool,
        items=[
            {"name": "Mysterious Potion", "true_name": "Potion of Healing", "kind": "potion"},
            {"name": "Mysterious Potion", "true_name": "Potion of Heroism", "kind": "potion"},
        ],
        identify_magic=False,
    )
    g._encounter_end_capture()

    recap = _last_encounter_recap_event(g)
    items = list((recap.data or {}).get("items_gained", []) or [])
    assert len(items) == 2
    assert items.count("Mysterious Potion") == 2


def test_encounter_end_capture_legacy_fallback_only_when_loot_pool_unavailable():
    # Explicit fallback path: no loot_pool state available.
    g_fallback = Game(HeadlessUI(), dice_seed=30280, wilderness_seed=30281)
    g_fallback.loot_pool = None
    g_fallback.party_items = [{"name": "Old Relic", "kind": "treasure"}]

    g_fallback._encounter_begin_capture(context="test")
    g_fallback.party_items.append({"name": "New Relic", "kind": "treasure"})
    g_fallback._encounter_end_capture()

    fallback_recap = _last_encounter_recap_event(g_fallback)
    fallback_items = list((fallback_recap.data or {}).get("items_gained", []) or [])
    assert "New Relic" in fallback_items

    # When loot_pool is available, do not silently prefer legacy party_items diffs.
    g_primary = Game(HeadlessUI(), dice_seed=30282, wilderness_seed=30283)
    g_primary._encounter_begin_capture(context="test")
    g_primary.party_items.append({"name": "Legacy Ghost", "kind": "treasure"})
    g_primary._encounter_end_capture()

    primary_recap = _last_encounter_recap_event(g_primary)
    primary_items = list((primary_recap.data or {}).get("items_gained", []) or [])
    assert primary_items == []

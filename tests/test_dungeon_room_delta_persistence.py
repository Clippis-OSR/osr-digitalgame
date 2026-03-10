from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict


def _new_game(seed: int = 1234) -> Game:
    g = Game(HeadlessUI(), dice_seed=seed)
    g._cmd_enter_dungeon()
    return g


def _find_room_id_by_type(g: Game, target: str) -> int:
    ids = sorted(int(k) for k in (g.dungeon_instance.blueprint.data.get("rooms") or {}).keys())
    for rid in ids:
        room = g._ensure_room(rid)
        if room.get("type") == target:
            return rid
    raise AssertionError(f"no room of type {target}")


def _find_room_for_loot(g: Game) -> int:
    for t in ("treasure", "treasury", "boss", "empty"):
        try:
            return _find_room_id_by_type(g, t)
        except AssertionError:
            continue
    raise AssertionError("no suitable room for loot test")


def _reload_room(g: Game, rid: int):
    g.dungeon_rooms.pop(int(rid), None)
    return g._ensure_room(int(rid))


def test_cleared_room_persists_across_leave_and_reentry():
    g = _new_game()
    rid = _find_room_id_by_type(g, "monster")
    room = g._ensure_room(rid)
    room["cleared"] = True
    room["foes"] = []
    g._sync_room_to_delta(rid, room)

    g.current_room_id = rid
    g._cmd_dungeon_leave()
    g.dungeon_rooms = {}
    g._cmd_enter_dungeon()

    room2 = _reload_room(g, rid)
    assert room2.get("cleared") is True
    assert not room2.get("foes")


def test_looted_treasure_and_door_state_persist_reentry():
    g = _new_game()
    rid = _find_room_for_loot(g)
    room = g._ensure_room(rid)
    room["treasure_taken"] = True
    room["cleared"] = True
    first_exit = next(iter((room.get("exits") or {}).keys()))
    room.setdefault("doors", {})[first_exit] = "open"
    g._sync_room_to_delta(rid, room)

    g._cmd_dungeon_leave()
    g.dungeon_rooms = {}
    g._cmd_enter_dungeon()

    room2 = _reload_room(g, rid)
    assert room2.get("treasure_taken") is True
    assert room2.get("cleared") is True
    assert room2.get("doors", {}).get(first_exit) == "open"


def test_triggered_trap_state_persists_reentry():
    g = _new_game()
    rid = _find_room_id_by_type(g, "trap")
    room = g._ensure_room(rid)
    room["trap_found"] = True
    room["trap_triggered"] = True
    room["trap_disarmed"] = True
    room["cleared"] = True
    g._sync_room_to_delta(rid, room)

    g._cmd_dungeon_leave()
    g.dungeon_rooms = {}
    g._cmd_enter_dungeon()

    room2 = _reload_room(g, rid)
    assert room2.get("trap_found") is True
    assert room2.get("trap_triggered") is True
    assert room2.get("trap_disarmed") is True
    assert room2.get("cleared") is True


def test_save_load_preserves_room_delta_state():
    g = _new_game()
    rid = _find_room_for_loot(g)
    room = g._ensure_room(rid)
    room["treasure_taken"] = True
    room["cleared"] = True
    room["feature_done"] = True
    g._sync_room_to_delta(rid, room)

    data = game_to_dict(g)

    g2 = Game(HeadlessUI(), dice_seed=1234)
    apply_game_dict(g2, data)
    room2 = _reload_room(g2, rid)
    assert room2.get("treasure_taken") is True
    assert room2.get("cleared") is True
    assert room2.get("feature_done") is True
    assert (g2.dungeon_instance.delta.data.get("rooms") or {}).get(str(rid), {}).get("feature_done") is True



def _room_ids_on_level(g: Game, level: int) -> list[int]:
    return list(g._bp_room_ids_on_level(int(level)))


def test_reenter_reuses_same_instance_identity_and_depth():
    g = _new_game(seed=777)
    inst_before = g.dungeon_instance
    did = str(inst_before.dungeon_id)
    seed = int(inst_before.seed)
    lvl = int(g.dungeon_level)

    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()

    inst_after = g.dungeon_instance
    assert inst_after is inst_before
    assert str(inst_after.dungeon_id) == did
    assert int(inst_after.seed) == seed
    assert int(g.dungeon_level) == lvl


def test_multiple_room_deltas_same_floor_survive_reentry():
    g = _new_game(seed=778)
    ids = _room_ids_on_level(g, 1)
    assert len(ids) >= 2
    r1, r2 = ids[0], ids[1]

    room1 = g._ensure_room(r1)
    room1["cleared"] = True
    room1["treasure_taken"] = True
    g._sync_room_to_delta(r1, room1)

    room2 = g._ensure_room(r2)
    room2["trap_found"] = True
    room2["trap_disarmed"] = True
    room2["cleared"] = True
    g._sync_room_to_delta(r2, room2)

    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()

    nr1 = _reload_room(g, r1)
    nr2 = _reload_room(g, r2)
    assert nr1.get("cleared") is True
    assert nr1.get("treasure_taken") is True
    assert nr2.get("trap_found") is True
    assert nr2.get("trap_disarmed") is True


def test_save_load_reenter_same_floor_preserves_overlays():
    g = _new_game(seed=779)
    ids = _room_ids_on_level(g, 1)
    rid = ids[0]
    room = g._ensure_room(rid)
    room["feature_done"] = True
    room["secret_found"] = True
    room["cleared"] = True
    g._sync_room_to_delta(rid, room)

    payload = game_to_dict(g)
    g2 = Game(HeadlessUI(), dice_seed=779)
    apply_game_dict(g2, payload)
    before_reenter = int(g2.dungeon_level)

    g2._cmd_dungeon_leave()
    g2._cmd_enter_dungeon()
    room2 = _reload_room(g2, rid)

    assert int(g2.dungeon_level) == before_reenter
    assert room2.get("feature_done") is True
    assert room2.get("secret_found") is True
    assert room2.get("cleared") is True


def test_depth_change_does_not_overwrite_other_floor_delta():
    g = _new_game(seed=780)
    if int(getattr(g, "max_dungeon_levels", 1)) < 2:
        return
    lvl1_ids = _room_ids_on_level(g, 1)
    lvl2_ids = _room_ids_on_level(g, 2)
    assert lvl1_ids and lvl2_ids
    rid1 = lvl1_ids[0]
    rid2 = lvl2_ids[0]

    room1 = g._ensure_room(rid1)
    room1["cleared"] = True
    room1["feature_done"] = True
    g._sync_room_to_delta(rid1, room1)

    g.dungeon_level = 2
    g.current_room_id = rid2
    room2 = g._ensure_room(rid2)
    room2["trap_found"] = True
    room2["trap_disarmed"] = True
    room2["cleared"] = True
    g._sync_room_to_delta(rid2, room2)

    # Return to floor 1 and verify floor-1 overlay wasn't overwritten by floor-2 updates.
    g.dungeon_level = 1
    g.current_room_id = rid1
    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()

    nr1 = _reload_room(g, rid1)
    nr2 = _reload_room(g, rid2)
    assert nr1.get("feature_done") is True
    assert nr1.get("cleared") is True
    assert nr2.get("trap_found") is True
    assert nr2.get("trap_disarmed") is True



def _special_ids(g: Game, tag: str) -> list[int]:
    out: list[int] = []
    for rid_s, rec in (g.dungeon_instance.blueprint.data.get("rooms") or {}).items():
        tags = [str(t).lower() for t in (rec or {}).get("tags", [])]
        if str(tag).lower() in tags:
            out.append(int(rid_s))
    out.sort()
    return out


def test_boss_and_treasury_placement_unique_and_stable_reentry():
    g = _new_game(seed=8123)
    boss_ids = _special_ids(g, "boss")
    treasury_ids = _special_ids(g, "treasury")

    assert len(boss_ids) == 1
    assert len(treasury_ids) <= 1

    boss_before = boss_ids[0]
    treasury_before = treasury_ids[0] if treasury_ids else None

    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()

    assert _special_ids(g, "boss") == [boss_before]
    assert _special_ids(g, "treasury") == ([treasury_before] if treasury_before is not None else [])


def test_treasury_reward_stable_across_save_load():
    g = _new_game(seed=8124)
    tids = _special_ids(g, "treasury")
    if not tids:
        return
    rid = tids[0]

    room = g._ensure_room(rid)
    gp_expected = int(room.get("treasure_gp", 0) or 0)
    items_expected = list(room.get("treasure_items") or [])

    payload = game_to_dict(g)
    g2 = Game(HeadlessUI(), dice_seed=8124)
    apply_game_dict(g2, payload)
    g2._cmd_dungeon_leave()
    g2._cmd_enter_dungeon()

    room2 = _reload_room(g2, rid)
    assert int(room2.get("treasure_gp", 0) or 0) == gp_expected
    assert list(room2.get("treasure_items") or []) == items_expected


def test_repeated_materialization_does_not_duplicate_unique_treasury_reward():
    g = _new_game(seed=8125)
    tids = _special_ids(g, "treasury")
    if not tids:
        return
    rid = tids[0]

    room = g._ensure_room(rid)
    before_gold = int(g.gold)
    before_items = len(g.party_items)
    g._handle_room_treasure(room)
    first_gold_gain = int(g.gold) - before_gold
    first_item_gain = len(g.party_items) - before_items
    assert first_gold_gain >= 0

    # Force rematerialization and try to loot again; reward should not duplicate.
    g.dungeon_rooms.pop(rid, None)
    room2 = g._ensure_room(rid)
    g._handle_room_treasure(room2)

    assert int(g.gold) - before_gold == first_gold_gain
    assert len(g.party_items) - before_items == first_item_gain


def test_floor_change_does_not_leak_special_flags_between_special_rooms():
    g = _new_game(seed=8126)
    boss_ids = _special_ids(g, "boss")
    treasury_ids = _special_ids(g, "treasury")
    if not boss_ids or not treasury_ids:
        return
    boss_id = boss_ids[0]
    treasury_id = treasury_ids[0]
    if int(g._bp_room_floor(boss_id) or 0) == int(g._bp_room_floor(treasury_id) or 0):
        return

    b = g._ensure_room(boss_id)
    b["cleared"] = True
    b["treasure_taken"] = True
    g._sync_room_to_delta(boss_id, b)

    t = g._ensure_room(treasury_id)
    t["treasure_taken"] = False
    t["cleared"] = False
    g._sync_room_to_delta(treasury_id, t)

    g.dungeon_level = int(g._bp_room_floor(treasury_id) or 0) + 1
    g.current_room_id = treasury_id
    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()

    rb = _reload_room(g, boss_id)
    rt = _reload_room(g, treasury_id)
    assert rb.get("treasure_taken") is True
    assert rb.get("cleared") is True
    assert rt.get("treasure_taken") is False



def _stocking_snapshot(g: Game) -> dict[int, tuple[int | None, str | None]]:
    out: dict[int, tuple[int | None, str | None]] = {}
    rooms = (g.dungeon_instance.stocking.data.get("rooms") or {})
    for rid_s, rec in rooms.items():
        if not isinstance(rec, dict):
            continue
        enc = rec.get("encounter") if isinstance(rec.get("encounter"), dict) else {}
        tr = rec.get("treasure") if isinstance(rec.get("treasure"), dict) else {}
        tcl = int(enc.get("target_cl", 0) or 0) if enc else None
        table = str(tr.get("table_id")) if tr and tr.get("table_id") else None
        out[int(rid_s)] = (tcl, table)
    return out


def _table_rank(table_id: str | None) -> int:
    return {"HOARD_A": 1, "HOARD_B": 2, "HOARD_C": 3}.get(str(table_id or ""), 0)


def _floor_scaling_stats(g: Game, floor0: int) -> tuple[float | None, float | None]:
    bp_rooms = g.dungeon_instance.blueprint.data.get("rooms") or {}
    srooms = g.dungeon_instance.stocking.data.get("rooms") or {}
    cls: list[int] = []
    tiers: list[int] = []
    for rid_s, brec in bp_rooms.items():
        if int((brec or {}).get("floor", 0) or 0) != int(floor0):
            continue
        srec = srooms.get(str(rid_s)) if isinstance(srooms, dict) else None
        if not isinstance(srec, dict):
            continue
        enc = srec.get("encounter") if isinstance(srec.get("encounter"), dict) else None
        tr = srec.get("treasure") if isinstance(srec.get("treasure"), dict) else None
        if enc and "target_cl" in enc:
            cls.append(int(enc.get("target_cl") or 0))
        if tr and tr.get("table_id"):
            tiers.append(_table_rank(str(tr.get("table_id"))))
    cl_avg = (sum(cls) / len(cls)) if cls else None
    tier_avg = (sum(tiers) / len(tiers)) if tiers else None
    return cl_avg, tier_avg


def test_depth_scaling_deeper_floors_are_higher_where_stocked():
    g = _new_game(seed=1234)
    floors = sorted({int((r or {}).get("floor", 0) or 0) for r in (g.dungeon_instance.blueprint.data.get("rooms") or {}).values()})
    assert floors
    lo, hi = floors[0], floors[-1]
    lo_cl, lo_tier = _floor_scaling_stats(g, lo)
    hi_cl, hi_tier = _floor_scaling_stats(g, hi)

    if lo_cl is not None and hi_cl is not None:
        assert hi_cl >= lo_cl
    if lo_tier is not None and hi_tier is not None:
        assert hi_tier >= lo_tier


def test_depth_scaling_snapshot_stable_across_reentry():
    g = _new_game(seed=9131)
    snap = _stocking_snapshot(g)
    g._cmd_dungeon_leave()
    g._cmd_enter_dungeon()
    assert _stocking_snapshot(g) == snap


def test_depth_scaling_snapshot_stable_across_save_load():
    g = _new_game(seed=9132)
    snap = _stocking_snapshot(g)
    payload = game_to_dict(g)

    g2 = Game(HeadlessUI(), dice_seed=9132)
    apply_game_dict(g2, payload)
    g2._cmd_dungeon_leave()
    g2._cmd_enter_dungeon()
    assert _stocking_snapshot(g2) == snap


def test_floor_change_does_not_leak_depth_scaling_state_between_floors():
    g = _new_game(seed=9133)
    snap = _stocking_snapshot(g)
    floors = sorted({int((r or {}).get("floor", 0) or 0) for r in (g.dungeon_instance.blueprint.data.get("rooms") or {}).values()})
    if len(floors) < 2:
        return

    lo, hi = floors[0], floors[-1]
    lo_ids = [rid for rid in g._bp_room_ids_on_level(lo + 1)]
    hi_ids = [rid for rid in g._bp_room_ids_on_level(hi + 1)]
    assert lo_ids and hi_ids

    # Materialize high-floor room then low-floor room; scaling snapshot must remain immutable.
    g.dungeon_level = hi + 1
    g.current_room_id = hi_ids[0]
    g._ensure_room(hi_ids[0])

    g.dungeon_level = lo + 1
    g.current_room_id = lo_ids[0]
    g._ensure_room(lo_ids[0])

    assert _stocking_snapshot(g) == snap

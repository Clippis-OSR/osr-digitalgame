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

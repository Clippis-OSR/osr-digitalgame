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

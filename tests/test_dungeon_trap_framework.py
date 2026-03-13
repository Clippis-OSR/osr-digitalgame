from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.models import Actor
from sww.save_load import game_to_dict, apply_game_dict


def _new_game(seed: int = 4242) -> Game:
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


def _find_room_for_trap_framework(g: Game) -> int:
    try:
        return _find_room_id_by_type(g, "trap")
    except AssertionError:
        ids = sorted(int(k) for k in (g.dungeon_instance.blueprint.data.get("rooms") or {}).keys())
        if not ids:
            raise
        rid = ids[0]
        room = g._ensure_room(rid)
        room["type"] = "trap"
        room["trap_kind"] = "pit"
        room["trap_desc"] = "The floor drops away into a pit!"
        room["trap_damage"] = "1d6"
        room["trap_disarmed"] = False
        return rid


def _find_neighbor(g: Game, rid: int) -> int:
    adj = g._dungeon_bp_adjacency() or {}
    nbs = list(adj.get(int(rid), []))
    if not nbs:
        raise AssertionError("trap room has no neighbors")
    return int(sorted(nbs)[0])


def _wire_open_exit(g: Game, src: int, dest: int) -> str:
    room = g._ensure_room(src)
    for k, v in (room.get("exits") or {}).items():
        if int(v) == int(dest):
            room.setdefault("doors", {})[str(k)] = "open"
            return str(k)
    key = "A"
    room.setdefault("exits", {})[key] = int(dest)
    room.setdefault("doors", {})[key] = "open"
    return key


def test_trap_passive_detection_records_found_and_warning_signs():
    g = _new_game(101)
    rid = _find_room_for_trap_framework(g)
    room = g._ensure_room(rid)
    room["entered"] = False
    room["trap_kind"] = "dart"
    room["trap"] = {"kind": "dart"}
    room["trap_found"] = False
    room["trap_disarmed"] = False

    scout = Actor(name="Scout", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)
    scout.thief_skills = {"Find/Remove Traps": 100}
    g.party.members = [scout]

    g._passive_room_entry_checks(room)

    assert room.get("trap_found") is True
    assert isinstance(room.get("trap"), dict)
    assert room["trap"].get("kind") == "dart"
    assert room["trap"].get("warning_signs")


def test_trap_triggers_deterministically_on_blind_entry():
    g = _new_game(202)
    trap_rid = _find_room_for_trap_framework(g)
    src = _find_neighbor(g, trap_rid)
    exit_key = _wire_open_exit(g, src, trap_rid)

    trap_room = g._ensure_room(trap_rid)
    trap_room["trap_kind"] = "pit"
    trap_room["trap"] = {"kind": "pit"}
    trap_room["trap_found"] = False
    trap_room["trap_triggered"] = False
    trap_room["trap_disarmed"] = False

    mover = Actor(name="Mover", hp=10, hp_max=10, ac_desc=8, hd=1, save=15, is_pc=True)
    g.party.members = [mover]
    g.current_room_id = src
    before_hp = g.party.living()[0].hp
    res = g._cmd_dungeon_move(exit_key)

    assert res.ok
    assert g.current_room_id == trap_rid
    assert trap_room.get("trap_triggered") is True
    assert trap_room.get("trap_disarmed") is True
    assert g.party.living()[0].hp < before_hp


def test_trap_state_persists_through_save_load_with_structured_trap_data():
    g = _new_game(303)
    rid = _find_room_for_trap_framework(g)
    room = g._ensure_room(rid)
    room["trap"] = {
        "kind": "gas",
        "found": True,
        "triggered": True,
        "disarmed": True,
        "disabled": True,
        "damage": "1d4",
        "warning_signs": ["A faint acrid smell hangs in the stale air."],
    }
    room["trap_kind"] = "gas"
    room["trap_found"] = True
    room["trap_triggered"] = True
    room["trap_disarmed"] = True
    g._sync_room_to_delta(rid, room)

    data = game_to_dict(g)
    g2 = Game(HeadlessUI(), dice_seed=303)
    apply_game_dict(g2, data)

    room2 = g2._ensure_room(rid)
    assert room2.get("trap_kind") == "gas"
    assert room2.get("trap_found") is True
    assert room2.get("trap_triggered") is True
    assert room2.get("trap_disarmed") is True
    assert (room2.get("trap") or {}).get("disabled") is True


def test_trap_resolution_deterministic_with_fixed_seed():
    def trap_snapshot(seed: int) -> tuple[str, str]:
        g = _new_game(seed)
        rid = _find_room_for_trap_framework(g)
        room = g._ensure_room(rid)
        room["trap_kind"] = room.get("trap_kind") or "dart"
        room["trap"] = {"kind": room["trap_kind"]}
        g._sync_room_to_delta(rid, room)
        room2 = g._ensure_room(rid)
        return str(room2.get("trap_kind") or ""), str((room2.get("trap") or {}).get("kind") or "")

    k1, t1 = trap_snapshot(999)
    k2, t2 = trap_snapshot(999)
    assert k1 == k2
    assert t1 == t2

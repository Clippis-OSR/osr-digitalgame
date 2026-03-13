from sww.game import Game
from sww.models import Actor, Stats
from sww.scripted_ui import ScriptedUI
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.dungeon_noncombat import (
    build_encounter,
    lint_encounter_effects,
    lint_authored_noncombat_content_for_ci,
    filter_ci_fatal_lint_issues,
)


def _new_game(seed: int = 7001, scripted: bool = False) -> Game:
    ui = ScriptedUI() if scripted else HeadlessUI()
    g = Game(ui, dice_seed=seed)
    g._cmd_enter_dungeon()
    pc = Actor(name="Scout", hp=8, hp_max=8, ac_desc=8, hd=1, save=15, is_pc=True)
    pc.stats = Stats(12, 11, 10, 10, 12, 10)
    g.party.members = [pc]
    g.light_on = True
    g.torch_turns_left = 5
    return g


def _find_room_for_noncombat(g: Game) -> int:
    ids = sorted(int(k) for k in (g.dungeon_instance.blueprint.data.get("rooms") or {}).keys())
    for rid in ids:
        room = g._ensure_room(rid)
        if room.get("type") in {"empty", "treasure", "monster"}:
            room["foes"] = []
            room["trap_triggered"] = False
            return rid
    rid = ids[0]
    room = g._ensure_room(rid)
    room["foes"] = []
    room["trap_triggered"] = False
    return rid


def _neighbor(g: Game, rid: int) -> int:
    adj = g._dungeon_bp_adjacency() or {}
    nbs = sorted(int(x) for x in (adj.get(int(rid), []) or []))
    assert nbs
    return nbs[0]


def _wire_open_exit(g: Game, src: int, dest: int) -> str:
    room = g._ensure_room(src)
    for k, v in (room.get("exits") or {}).items():
        if int(v) == int(dest):
            room.setdefault("doors", {})[str(k)] = "open"
            return str(k)
    room.setdefault("exits", {})["A"] = int(dest)
    room.setdefault("doors", {})["A"] = "open"
    return "A"


def test_noncombat_encounter_generation_and_hint_visibility():
    g = _new_game(7002)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    enc = g._ensure_room_noncombat_encounter(room)

    assert isinstance(enc, dict)
    assert enc.get("archetype") in {"stranded_npc", "neutral_creature", "omen_echo", "environmental_scene"}
    assert g._dungeon_hazard_hint(room) in {"Non-combat encounter present.", "Known hazard here."}


def test_noncombat_choice_resolution_updates_state_and_history():
    g = _new_game(7003, scripted=True)
    assert isinstance(g.ui, ScriptedUI)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    room["noncombat_encounter"] = {
        "id": f"nc:test:{rid}",
        "archetype": "neutral_creature",
        "status": "active",
        "state": {"observed": False},
        "prompt": "A neutral creature waits.",
        "choices": [
            {"id": "observe", "label": "Observe quietly", "effects": ["mark:observed"], "resolve": False},
            {"id": "retreat", "label": "Back away", "effects": [], "resolve": True},
        ],
        "history": [],
    }

    g.ui.push(0)
    res = g._cmd_dungeon_interact_encounter()

    assert res.ok
    enc = room.get("noncombat_encounter") or {}
    assert enc.get("status") == "active"
    assert (enc.get("state") or {}).get("observed") is True
    assert len(enc.get("history") or []) == 1


def test_noncombat_partial_state_persists_leave_return_and_save_load():
    g = _new_game(7004, scripted=True)
    assert isinstance(g.ui, ScriptedUI)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    room["noncombat_encounter"] = {
        "id": f"nc:test:{rid}",
        "archetype": "neutral_creature",
        "status": "active",
        "state": {"observed": False},
        "prompt": "A neutral creature waits.",
        "choices": [
            {"id": "observe", "label": "Observe quietly", "effects": ["mark:observed"], "resolve": False},
        ],
        "history": [],
    }
    g.current_room_id = rid
    g.ui.push(0)
    assert g._cmd_dungeon_interact_encounter().ok

    nb = _neighbor(g, rid)
    to_nb = _wire_open_exit(g, rid, nb)
    to_rid = _wire_open_exit(g, nb, rid)
    assert g._cmd_dungeon_move(to_nb).ok
    assert g._cmd_dungeon_move(to_rid).ok

    room_back = g._ensure_room(rid)
    assert (room_back.get("noncombat_encounter") or {}).get("status") == "active"
    assert ((room_back.get("noncombat_encounter") or {}).get("state") or {}).get("observed") is True

    data = game_to_dict(g)
    g2 = _new_game(7004)
    apply_game_dict(g2, data)
    room2 = g2._ensure_room(rid)
    assert ((room2.get("noncombat_encounter") or {}).get("state") or {}).get("observed") is True
    assert (room2.get("noncombat_encounter") or {}).get("status") == "active"




def test_builtin_archetype_effects_lint_clean():
    g = _new_game(7007)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    # Validate canonical archetype templates directly.
    for archetype in ("stranded_npc", "neutral_creature", "omen_echo", "environmental_scene"):
        enc = build_encounter(archetype, room=room, ctx={"room_id": rid, "depth": int(room.get("depth", 1) or 1)})
        issues = lint_encounter_effects(enc)
        assert issues == []


def test_unknown_effect_type_is_flagged_by_lint():
    enc = {
        "choices": [
            {"effects": [{"type": "mystery_effect", "x": 1}]},
        ]
    }
    issues = lint_encounter_effects(enc)
    assert any(i.get("code") == "unknown_effect_type" for i in issues)
    assert filter_ci_fatal_lint_issues(issues)


def test_malformed_known_effect_is_flagged_by_lint():
    enc = {
        "choices": [
            {"effects": [{"type": "heal", "amount": "two"}]},
        ]
    }
    issues = lint_encounter_effects(enc)
    assert any(i.get("code") == "malformed_param_type" for i in issues)
    assert filter_ci_fatal_lint_issues(issues)


def test_missing_required_param_is_flagged_by_lint_and_ci_fatal_filter():
    enc = {
        "choices": [
            {"effects": [{"type": "heal"}]},
        ]
    }
    issues = lint_encounter_effects(enc)
    assert any(i.get("code") == "missing_required_param" for i in issues)
    assert filter_ci_fatal_lint_issues(issues)


def test_legacy_string_effect_lints_warning_but_remains_runtime_safe():
    enc = {
        "choices": [
            {"effects": ["mark:observed"]},
        ]
    }
    issues = lint_encounter_effects(enc)
    assert any(i.get("code") == "legacy_effect_string" for i in issues)
    assert filter_ci_fatal_lint_issues(issues) == []


def test_ci_facing_builtin_content_lint_passes_clean():
    assert lint_authored_noncombat_content_for_ci() == []



def test_invalid_effect_slips_through_runtime_with_noop_safety_and_lint_event():
    g = _new_game(7008, scripted=True)
    assert isinstance(g.ui, ScriptedUI)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    room["noncombat_encounter"] = {
        "id": f"nc:test:{rid}",
        "archetype": "neutral_creature",
        "status": "active",
        "state": {},
        "prompt": "Invalid effect safety.",
        "choices": [
            {"id": "x", "label": "Do", "effects": [{"type": "unknown_x", "foo": 1}], "resolve": True},
        ],
        "history": [],
    }
    g.ui.push(0)
    res = g._cmd_dungeon_interact_encounter()
    assert res.ok
    assert (room.get("noncombat_encounter") or {}).get("status") == "resolved"
    assert any(str(getattr(evt, "name", "")) == "dungeon_noncombat_effect_lint" for evt in (getattr(g.events, "events", []) or []))


def test_noncombat_effects_are_typed_and_registry_dispatched():
    g = _new_game(7005, scripted=True)
    assert isinstance(g.ui, ScriptedUI)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    room["noncombat_encounter"] = {
        "id": f"nc:test:{rid}",
        "archetype": "stranded_npc",
        "status": "active",
        "state": {},
        "prompt": "A stranded delver waits.",
        "choices": [
            {
                "id": "aid",
                "label": "Aid",
                "effects": [
                    {"type": "ration", "delta": -1},
                    {"type": "heal", "amount": 2},
                    {"type": "mark", "key": "aided", "value": True},
                ],
                "resolve": True,
            }
        ],
        "history": [],
    }
    g.rations = 3
    g.party.members[0].hp = 5
    g.ui.push(0)
    assert g._cmd_dungeon_interact_encounter().ok
    enc = room.get("noncombat_encounter") or {}
    assert g.rations == 2
    assert g.party.members[0].hp >= 7
    assert (enc.get("state") or {}).get("aided") is True


def test_legacy_string_effects_normalize_on_load_and_resolve():
    g = _new_game(7006, scripted=True)
    assert isinstance(g.ui, ScriptedUI)
    rid = _find_room_for_noncombat(g)
    room = g._ensure_room(rid)
    room["noncombat_encounter"] = {
        "id": f"nc:test:{rid}",
        "archetype": "neutral_creature",
        "status": "active",
        "state": {},
        "prompt": "Legacy shape encounter.",
        "choices": [
            {"id": "observe", "label": "Observe", "effects": ["mark:observed"], "resolve": False},
        ],
        "history": [],
    }
    g.ui.push(0)
    assert g._cmd_dungeon_interact_encounter().ok
    eff = ((room.get("noncombat_encounter") or {}).get("choices") or [])[0].get("effects")
    assert isinstance(eff[0], dict)
    assert eff[0].get("type") == "mark"
    assert ((room.get("noncombat_encounter") or {}).get("state") or {}).get("observed") is True


def test_noncombat_generation_deterministic_with_fixed_seed():
    def snapshot(seed: int) -> tuple[str, str]:
        g = _new_game(seed)
        rid = _find_room_for_noncombat(g)
        room = g._ensure_room(rid)
        enc = g._ensure_room_noncombat_encounter(room) or {}
        return str(enc.get("id") or ""), str(enc.get("archetype") or "")

    a = snapshot(7010)
    b = snapshot(7010)
    assert a == b

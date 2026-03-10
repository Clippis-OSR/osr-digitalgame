"""Non-interactive self-test suite.

Run:
    python main.py --selftest

The goal is to catch regressions in the core engine without requiring UI input.
This is not a full unit test framework; it is a deterministic-ish smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple
import os
import tempfile


def _multiset_of_dicts(items: list[dict[str, Any]]) -> dict[tuple, int]:
    """Stable multiset for lists of dicts.

    Party inventory allows duplicates (e.g. two torches). We want to ensure
    *counts* don't change across save/load.
    """
    out: dict[tuple, int] = {}
    for it in items or []:
        if not isinstance(it, dict):
            key = ("__non_dict__", str(it))
        else:
            key = tuple(sorted((str(k), str(v)) for k, v in it.items()))
        out[key] = out.get(key, 0) + 1
    return out


def _room_fingerprint(room: dict[str, Any]) -> dict[str, Any]:
    """Create a stable fingerprint for a dungeon room.

    Focuses on fields that should *not* change across save/load.
    """
    if not isinstance(room, dict):
        return {"_bad_room": True}

    fp: dict[str, Any] = {
        "id": int(room.get("id", 0) or 0),
        "type": str(room.get("type", "")),
        "cleared": bool(room.get("cleared", False)),
        "secret_found": bool(room.get("secret_found", False)),
        "trap_disarmed": bool(room.get("trap_disarmed", False)),
        "treasure_taken": bool(room.get("treasure_taken", False)),
        "exits": dict(room.get("exits") or {}),
        "doors": dict(room.get("doors") or {}),
        "stairs": dict(room.get("stairs") or {}),
    }

    foes = room.get("foes") or []
    foes_fp = []
    if isinstance(foes, list):
        for f in foes:
            try:
                foes_fp.append({
                    "name": str(getattr(f, "name", "")),
                    "hp": int(getattr(f, "hp", 0)),
                    "hp_max": int(getattr(f, "hp_max", 0)),
                    "ac_desc": int(getattr(f, "ac_desc", 0)),
                    "hd": int(getattr(f, "hd", 0)),
                    "save": int(getattr(f, "save", 0)),
                    "weapon": str(getattr(f, "weapon", "")),
                    "race": str(getattr(f, "race", "")),
                    "monster_id": str(getattr(f, "monster_id", "")),
                })
            except Exception:
                foes_fp.append({"name": str(getattr(f, "name", "?"))})

    foes_fp = sorted(foes_fp, key=lambda d: (d.get("name", ""), d.get("hp", 0), d.get("ac_desc", 0)))
    fp["foes"] = foes_fp
    fp["foes_n"] = len(foes_fp)
    return fp


class DummyUI:
    """UI stub to make Game logic non-interactive for testing."""

    def __init__(self):
        self.auto_combat = True
        self.logs: list[str] = []

    def title(self, t: str):
        self.logs.append(f"=={t}==")

    def log(self, msg: Any):
        self.logs.append(str(msg))

    def hr(self):
        # Horizontal rule / spacing (no-op for tests)
        self.logs.append("---")

    def choose(self, prompt: str, options: list[str]):
        # Always choose the first option.
        return 0


class QueueUI(DummyUI):
    """UI stub that returns a scripted sequence of choices."""

    def __init__(self, choices: list[int]):
        super().__init__()
        self._choices = list(choices)

    def choose(self, prompt: str, options: list[str]):
        if self._choices:
            v = int(self._choices.pop(0))
            # Clamp to valid range to avoid accidental IndexError loops.
            if v < 0:
                return 0
            if v >= len(options):
                return len(options) - 1
            return v
        return super().choose(prompt, options)


def _mk_basic_party(game) -> None:
    from .game import PC
    from .models import Stats

    # Two basic PCs with decent stats.
    p1 = PC(name="Tester A", hp=18, hp_max=18, ac_desc=4, hd=1, save=14, is_pc=True,
            stats=Stats(16, 12, 12, 10, 10, 10), weapon="sword")
    p2 = PC(name="Tester B", hp=16, hp_max=16, ac_desc=5, hd=1, save=14, is_pc=True,
            stats=Stats(14, 12, 12, 10, 10, 10), weapon="mace")
    game.party.members = [p1, p2]
    # Ensure some supplies.
    game.rations = 24
    game.torches = 3


def _test_combat(game) -> None:
    """Fast, deterministic-ish combat smoke test.

    Uses tiny dummy foes to ensure the combat loop terminates quickly.
    """
    from .models import Actor

    game.light_on = True

    foes = [
        Actor(name="Test Rat", hp=1, hp_max=1, ac_desc=8, hd=1, save=16, race="Beast", is_pc=False, weapon="bite"),
        Actor(name="Test Rat", hp=1, hp_max=1, ac_desc=8, hd=1, save=16, race="Beast", is_pc=False, weapon="bite"),
    ]
    game.combat(foes)
    assert all(isinstance(m.hp, int) for m in game.party.members)
    assert all(isinstance(f.hp, int) for f in foes)


def _test_town_shop_purchase_and_save_load(_game) -> None:
    """P6.0.0.1: minimal regression for town shop + save/load roundtrip."""

    from .game import Game
    from .save_load import save_game, load_game

    # Choose a 1gp+ weapon (index 6 after sorting by cost/name) so gold decreases.
    ui = QueueUI([0, 0, 6, 2])
    g1 = Game(ui, dice_seed=31337, wilderness_seed=424242)
    g1.enable_validation = True
    _mk_basic_party(g1)

    g1.gold = 10_000
    before_gold = int(g1.gold)
    before_weapon = str(getattr(g1.party.members[0], "weapon", ""))
    before_items_ms = _multiset_of_dicts(list(getattr(g1, "party_items", []) or []))

    g1.town_arms_store()

    after_weapon = str(getattr(g1.party.members[0], "weapon", ""))
    assert after_weapon and after_weapon != before_weapon, "Expected weapon to change after purchase"
    assert int(g1.gold) < before_gold, "Expected gold to decrease after purchase"

    after_items_ms = _multiset_of_dicts(list(getattr(g1, "party_items", []) or []))
    assert after_items_ms == before_items_ms, "Unexpected mutation of party_items during store purchase"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "selftest_save.json")
        save_game(g1, path)

        ui2 = DummyUI()
        g2 = Game(ui2, dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        load_game(g2, path)

        assert int(getattr(g2, "gold", -1)) == int(g1.gold), "Gold mismatch after save/load"
        assert str(getattr(g2.party.members[0], "weapon", "")) == after_weapon, "Weapon mismatch after save/load"

        # Invariants: no duplication / drift
        g1_items = list(getattr(g1, "party_items", []) or [])
        g2_items = list(getattr(g2, "party_items", []) or [])
        assert len(g2_items) == len(g1_items), "party_items count drift after load"
        assert _multiset_of_dicts(g2_items) == _multiset_of_dicts(g1_items), "party_items multiset drift after load"
        assert len(getattr(g2, "contract_offers", [])) == len(getattr(g1, "contract_offers", [])), "contract_offers drift after load"
        assert len(getattr(g2, "active_contracts", [])) == len(getattr(g1, "active_contracts", [])), "active_contracts drift after load"


def _test_save_schema_error_paths(_game) -> None:
    """P6.0.0.8: schema guardrails must report correct JSON path in STRICT_CONTENT mode.

    This is a targeted regression test for the save schema validator.
    """

    import json
    from .game import Game
    from .save_load import save_game, load_game, SaveSchemaError

    ui = DummyUI()
    g1 = Game(ui, dice_seed=9001, wilderness_seed=9002)
    g1.enable_validation = True
    _mk_basic_party(g1)

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "malformed_save.json")
        save_game(g1, path)

        # Corrupt a required subtree: campaign.party.members must be an array.
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("campaign", {})
        data["campaign"].setdefault("party", {})
        data["campaign"]["party"]["members"] = {"not": "a list"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        old = os.environ.get("STRICT_CONTENT")
        os.environ["STRICT_CONTENT"] = "true"
        try:
            g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
            g2.enable_validation = True
            try:
                load_game(g2, path)
                raise AssertionError("Expected load_game to raise SaveSchemaError for malformed save")
            except SaveSchemaError as e:
                msg = str(e)
                assert "campaign.party.members" in msg, f"Expected schema error path in message, got: {msg}"
                assert "expected array" in msg or "missing required array" in msg, f"Expected type hint in message, got: {msg}"
        finally:
            if old is None:
                os.environ.pop("STRICT_CONTENT", None)
            else:
                os.environ["STRICT_CONTENT"] = old


def _determinism_snapshot(g) -> dict[str, Any]:
    """Capture a small, stable snapshot to detect nondeterminism.

    This intentionally exercises both wilderness hex generation and dungeon room
    stocking while remaining fast.
    """

    from .wilderness import ensure_hex
    import json, hashlib

    positions = [(0, 0), (1, 0), (0, 1), (2, -1), (-1, 1)]
    hexes: dict[str, Any] = {}
    for (q, r) in positions:
        ensure_hex(g.world_hexes, (q, r), g.wilderness_rng)
        k = f"{q},{r}"
        hexes[k] = g.world_hexes.get(k)

    # Dungeon: stock a non-starting room.
    room2 = g._ensure_room(2)
    # P6.1: blueprint determinism (town default dungeon)
    from .dungeon_instance import ensure_instance
    g.dungeon_instance = ensure_instance(g.dungeon_instance, "town_dungeon_01", g.dice_seed)
    bp = g.dungeon_instance.blueprint.data
    bp_hash = hashlib.sha256(json.dumps(bp, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "hexes": hexes,
        "room2": _room_fingerprint(room2),
        "blueprint_hash": bp_hash,
        "stocking_hash": hashlib.sha256(json.dumps((g.dungeon_instance.stocking.data if (g.dungeon_instance and g.dungeon_instance.stocking) else {}), sort_keys=True).encode("utf-8")).hexdigest(),
    }


def _test_determinism_gate_same_seed(_game) -> None:
    """P6.0.0.9: determinism gate.

    With identical seeds, key generated artifacts must match exactly.
    """

    from .game import Game

    seed = 24680
    g1 = Game(DummyUI(), dice_seed=seed, wilderness_seed=seed + 11111)
    g1.enable_validation = True
    _mk_basic_party(g1)
    s1 = _determinism_snapshot(g1)

    g2 = Game(DummyUI(), dice_seed=seed, wilderness_seed=seed + 11111)
    g2.enable_validation = True
    _mk_basic_party(g2)
    s2 = _determinism_snapshot(g2)

    assert s1 == s2, "Determinism gate failed: same seed produced different snapshot"





def _test_retainers_and_contracts_no_dup_save_load(_game) -> None:
    """P6.0.0.6: deep no-dup checks for retainers + contracts across save/load."""

    from .game import Game
    from .save_load import save_game, load_game

    ui = DummyUI()
    g1 = Game(ui, dice_seed=777, wilderness_seed=888)
    g1.enable_validation = True
    _mk_basic_party(g1)

    # --- Retainers: hire one and assign to expedition using shared object refs ---
    g1.gold = 10_000
    g1.generate_retainer_board(force=True)
    assert getattr(g1, "retainer_board", None), "Expected retainer board to be populated"
    r = g1.retainer_board.pop(0)
    g1.hired_retainers.append(r)
    g1.retainer_roster.append(r)
    r.on_expedition = True
    g1.active_retainers.append(r)

    # --- Contracts: generate and accept one ---
    g1.last_contract_day = int(getattr(g1, "campaign_day", 0))
    from .contracts import generate_contracts
    g1.contract_offers = [c.to_dict() for c in generate_contracts(
        g1.wilderness_rng, g1.factions, g1.world_hexes, g1.campaign_day, n=3
    )]
    assert g1.contract_offers, "Expected contract offers to be generated"
    cid = str(g1.contract_offers[0].get("cid", ""))
    res = g1._cmd_accept_contract(cid)
    assert getattr(res, "status", "") in ("ok", "info"), "Expected accept contract to succeed"

    # Uniqueness pre-save
    def _unique(seq):
        return len(set(seq)) == len(seq)

    assert _unique([x.get("cid") for x in (g1.contract_offers or []) if isinstance(x, dict)]), "Duplicate cids in contract_offers pre-save"
    assert _unique([x.get("cid") for x in (g1.active_contracts or []) if isinstance(x, dict)]), "Duplicate cids in active_contracts pre-save"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "retainers_contracts_save.json")
        save_game(g1, path)

        g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        load_game(g2, path)

        # --- Retainer invariants post-load ---
        roster = list(getattr(g2, "retainer_roster", []) or [])
        hired = list(getattr(g2, "hired_retainers", []) or [])
        active = list(getattr(g2, "active_retainers", []) or [])

        roster_names = [getattr(a, "name", "") for a in roster]
        hired_names = [getattr(a, "name", "") for a in hired]
        active_names = [getattr(a, "name", "") for a in active]

        assert _unique(roster_names), "Duplicate retainer names in roster after load"
        assert _unique(hired_names), "Duplicate retainer names in hired after load"
        assert _unique(active_names), "Duplicate retainer names in active after load"

        assert set(hired_names).issubset(set(roster_names)), "Hired retainers must be subset of roster after load"
        assert set(active_names).issubset(set(hired_names)), "Active retainers must be subset of hired after load"
        assert active_names, "Expected at least one active retainer after load"
        for a in active:
            assert bool(getattr(a, "on_expedition", False)), "Active retainer must have on_expedition=True after load"

        # Ensure shared object reference for the hired/active retainer equals roster entry
        name0 = active_names[0]
        roster_obj = next(a for a in roster if getattr(a, "name", "") == name0)
        hired_obj = next(a for a in hired if getattr(a, "name", "") == name0)
        active_obj = next(a for a in active if getattr(a, "name", "") == name0)
        assert roster_obj is hired_obj and hired_obj is active_obj, "Retainer objects should be interned across lists after load"

        # --- Contract invariants post-load ---
        offers = [x for x in (getattr(g2, "contract_offers", []) or []) if isinstance(x, dict)]
        actives = [x for x in (getattr(g2, "active_contracts", []) or []) if isinstance(x, dict)]
        assert _unique([x.get("cid") for x in offers]), "Duplicate cids in contract_offers after load"
        assert _unique([x.get("cid") for x in actives]), "Duplicate cids in active_contracts after load"

def _test_dungeon_enter_and_save_load(_game) -> None:
    """P6.0.0.2: enter dungeon via command flow, advance a turn, save/load roundtrip."""

    from .game import Game
    from .commands import EnterDungeon, DungeonAdvanceTurn
    from .save_load import save_game, load_game

    ui = DummyUI()
    g1 = Game(ui, dice_seed=111, wilderness_seed=222)
    g1.enable_validation = True
    _mk_basic_party(g1)

    # Enter dungeon and advance one turn deterministically.
    res_enter = g1.dispatch(EnterDungeon())
    assert res_enter is not None and getattr(res_enter, "status", "") in ("ok", "info"), "EnterDungeon should return a CommandResult"
    assert int(g1.current_room_id) == 1, "Expected to start in room 1 after EnterDungeon"

    # Force-generate a second room so we can test that monsters/treasure do not regenerate across load.
    # Room 2 is the most likely stable candidate due to deterministic ID allocation.
    r2 = g1._ensure_room(2)
    fp_r2_before = _room_fingerprint(r2)
    before_turn = int(getattr(g1, "dungeon_turn", 0))
    res_turn = g1.dispatch(DungeonAdvanceTurn())
    assert res_turn is not None and getattr(res_turn, "status", "") in ("ok", "info"), "DungeonAdvanceTurn should return a CommandResult"
    assert int(getattr(g1, "dungeon_turn", 0)) >= before_turn, "Dungeon turn should not go backwards"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "selftest_dungeon_save.json")
        save_game(g1, path)

        g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        load_game(g2, path)

        assert int(g2.current_room_id) == int(g1.current_room_id), "Room mismatch after save/load"
        assert int(getattr(g2, "dungeon_turn", -1)) == int(getattr(g1, "dungeon_turn", -2)), "Dungeon turn mismatch after save/load"
        assert isinstance(getattr(g2, "dungeon_rooms", None), dict), "Dungeon rooms missing after load"
        assert all(not (isinstance(k, str) and str(k).isdigit()) for k in (getattr(g2, "dungeon_rooms", {}) or {}).keys()), \
            "Dungeon room keys should be normalized to ints after load"

        k1 = {int(k) for k in getattr(g1, "dungeon_rooms", {}).keys()}
        k2 = {int(k) for k in getattr(g2, "dungeon_rooms", {}).keys()}
        assert k1 == k2, "Dungeon rooms keys mismatch"

        # Deep no-regeneration check: room content should match exactly.
        rooms2 = (getattr(g2, "dungeon_rooms", {}) or {})
        r2_after = rooms2.get(2) or rooms2.get("2") or {}
        fp_r2_after = _room_fingerprint(r2_after)
        assert fp_r2_after == fp_r2_before, "Room 2 content drift/regeneration after save/load"

        # Also ensure that re-entering the same room doesn't reroll content (visited may increment).
        r2_again = g2._ensure_room(2)
        fp_r2_again = _room_fingerprint(r2_again)
        assert fp_r2_again == fp_r2_before, "Room 2 content mutated after ensure_room; expected stable content"


def _test_dungeon_stairs_travel_and_persist(_game) -> None:
    """P6.1.1+: stairs should change dungeon_level and land in a sensible destination room.

    Also verifies save/load preserves (level, room) after stairs traversal.
    """

    from .game import Game
    from .commands import EnterDungeon, DungeonUseStairs
    from .save_load import save_game, load_game

    g1 = Game(DummyUI(), dice_seed=333, wilderness_seed=444)
    g1.enable_validation = True
    _mk_basic_party(g1)

    res_enter = g1.dispatch(EnterDungeon())
    assert res_enter is not None and getattr(res_enter, "status", "") in ("ok", "info"), "EnterDungeon should return a CommandResult"

    # Must be multi-level if blueprint floors exist.
    assert int(getattr(g1, "max_dungeon_levels", 1)) >= 2, "Expected blueprint-driven dungeon to expose >=2 levels"

    # Find a level-1 room that has stairs_down.
    down_room = g1._bp_pick_room_with_tag_on_level(tag="stairs_down", level=1)
    assert down_room is not None, "Expected at least one stairs_down room on level 1"
    g1.current_room_id = int(down_room)
    r = g1._ensure_room(int(down_room))
    assert bool((r.get("stairs") or {}).get("down")), "Chosen room should have stairs down in runtime view"

    res_down = g1.dispatch(DungeonUseStairs("down"))
    assert res_down is not None and getattr(res_down, "status", "") in ("ok", "info"), "DungeonUseStairs(down) should return ok"
    assert int(getattr(g1, "dungeon_level", 1)) == 2, "Expected dungeon_level=2 after using stairs down"

    # Landing room should be on level 2 and have stairs_up.
    landing = int(getattr(g1, "current_room_id", 0) or 0)
    assert landing > 0, "Expected a valid landing room id"
    meta = g1._dungeon_room_meta(landing)
    assert int(meta.get("floor", 0)) == 1, "Landing room should be on floor=1 (level 2)"
    tags = [t.lower() for t in (g1._dungeon_room_tags(landing) or [])]
    assert "stairs_up" in tags, "Landing room should have stairs_up tag"

    # Now go back up.
    g1._ensure_room(landing)
    res_up = g1.dispatch(DungeonUseStairs("up"))
    assert res_up is not None and getattr(res_up, "status", "") in ("ok", "info"), "DungeonUseStairs(up) should return ok"
    assert int(getattr(g1, "dungeon_level", 1)) == 1, "Expected dungeon_level=1 after using stairs up"

    # Save/load should preserve the current (level, room).
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "selftest_stairs_save.json")
        save_game(g1, path)

        g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        load_game(g2, path)

        assert int(getattr(g2, "dungeon_level", -1)) == int(getattr(g1, "dungeon_level", -2)), "Dungeon level mismatch after stairs save/load"
        assert int(getattr(g2, "current_room_id", -1)) == int(getattr(g1, "current_room_id", -2)), "Room mismatch after stairs save/load"


def _test_dungeon_traps_find_disarm_and_persist(_game) -> None:
    """P6.1.2.0: traps support find->disarm and persist through save/load via delta."""

    from .game import Game, PC
    from .models import Stats
    from .commands import EnterDungeon, DungeonSearchTraps
    from .save_load import save_game, load_game

    g1 = Game(DummyUI(), dice_seed=555, wilderness_seed=666)
    g1.enable_validation = True

    thief = PC(
        name="Thief",
        hp=6,
        hp_max=6,
        ac_desc=7,
        hd=1,
        save=14,
        is_pc=True,
        cls="Thief",
        level=1,
        stats=Stats(10, 14, 10, 10, 10, 10),
        weapon="dagger",
    )
    # Make trap handling effectively guaranteed.
    thief.thief_skills = {"Find/Remove Traps": 99}
    g1.party.members = [thief]

    res_enter = g1.dispatch(EnterDungeon())
    assert res_enter is not None and getattr(res_enter, "status", "") in ("ok", "info")

    # Pick any trap room across levels; if none exist, the blueprint/stocking is malformed.
    trap_room_id = None
    max_lvl = int(getattr(g1, "max_dungeon_levels", 1) or 1)
    for lvl in range(1, max_lvl + 1):
        trap_room_id = g1._bp_pick_room_with_tag_on_level(tag="trap", level=lvl)
        if trap_room_id is not None:
            break
    assert trap_room_id is not None, "Expected at least one trap room in the dungeon"

    # Teleport directly to avoid entry-trigger chance; we're testing search/disarm flow.
    g1.current_room_id = int(trap_room_id)
    room = g1._ensure_room(int(trap_room_id))
    assert room.get("type") == "trap" or room.get("trap_desc"), "Expected a runtime trap room"

    # First search should discover.
    g1.dispatch(DungeonSearchTraps())
    room2 = g1._ensure_room(int(trap_room_id))
    assert bool(room2.get("trap_found")) is True, "Trap should be discovered after searching"
    assert bool(room2.get("trap_disarmed")) is False, "Trap should not be auto-disarmed on first find"

    # Second search should disarm.
    g1.dispatch(DungeonSearchTraps())
    room3 = g1._ensure_room(int(trap_room_id))
    assert bool(room3.get("trap_disarmed")) is True, "Trap should be disarmed"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "selftest_trap_save.json")
        save_game(g1, path)
        g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        load_game(g2, path)
        g2.current_room_id = int(trap_room_id)
        roomL = g2._ensure_room(int(trap_room_id))
        assert bool(roomL.get("trap_found")) is True, "trap_found should persist after load"
        assert bool(roomL.get("trap_disarmed")) is True, "trap_disarmed should persist after load"


def _test_dungeon_listen_at_closed_door(_game) -> None:
    """P6.1.4.1: Listen at doors should require a closed door and give a useful hint.

    The listen roll is forced to succeed to keep the test deterministic.
    """
    from .game import Game
    from .commands import EnterDungeon, DungeonListen

    ui = QueueUI([0])
    g = Game(ui, dice_seed=777, wilderness_seed=888)
    g.enable_validation = True
    _mk_basic_party(g)

    res_enter = g.dispatch(EnterDungeon())
    assert res_enter is not None and getattr(res_enter, "status", "") in ("ok", "info")

    room = g.dungeon_rooms.get(g.current_room_id) or g._ensure_room(g.current_room_id)
    exits = list((room.get("exits") or {}).keys())
    assert exits, "Dungeon start room should have at least one exit"
    exit_key = exits[0]

    # Ensure the chosen exit is a closed door.
    if isinstance(room.get("doors"), dict):
        room["doors"][exit_key] = "closed"
        try:
            g._sync_room_to_delta(int(room.get("id", g.current_room_id)), room)
        except Exception:
            pass

    neighbor_id = int((room.get("exits") or {}).get(exit_key))
    inst = getattr(g, "dungeon_instance", None)
    assert inst is not None

    # Force monsters stocked beyond the door.
    rooms = (inst.stocking.data or {}).setdefault("rooms", {})
    rec = rooms.get(str(neighbor_id)) if isinstance(rooms.get(str(neighbor_id)), dict) else {}
    rec["encounter"] = {"monster_id": "goblin", "count": 2, "target_cl": 1}
    rooms[str(neighbor_id)] = rec

    drooms = (inst.delta.data or {}).setdefault("rooms", {})
    drec = drooms.get(str(neighbor_id)) if isinstance(drooms.get(str(neighbor_id)), dict) else {}
    drec["cleared"] = False
    drooms[str(neighbor_id)] = drec

    # Force listen roll success.
    orig_d = g.dice.d
    try:
        g.dice.d = lambda sides: 1 if int(sides) == 6 else orig_d(int(sides))
        g.dispatch(DungeonListen())
    finally:
        g.dice.d = orig_d

    joined = "\n".join(ui.logs).lower()
    assert "hear movement" in joined, "Listening should hint at movement when monsters are present beyond"




def _test_dungeon_pick_lock_requires_skill_and_persists(_game) -> None:
    """P6.1.4.3: Locked doors + Pick Lock using Open Locks % skill (Thief/Monk/etc.).

    - Dungeon blueprint must contain at least one locked edge (generator ensures this when possible).
    - Only a party member with Open Locks % may pick the lock; otherwise forcing is required.
    - Door open state should mirror on both sides and persist after save/load.
    """
    import os, tempfile
    from .game import Game, PC
    from .models import Stats
    from .commands import EnterDungeon, DungeonDoorAction
    from .save_load import save_game, load_game

    ui = QueueUI([])
    g = Game(ui, dice_seed=12345, wilderness_seed=54321)
    g.enable_validation = True

    # Party includes a monk with Open Locks 100%.
    thief = PC(name="Lockpicker", hp=10, hp_max=10, ac_desc=7, hd=1, save=16, is_pc=True,
               stats=Stats(10, 15, 10, 10, 10, 10), weapon="dagger")
    thief.cls = 'Monk'
    thief.thief_skills = {'Open Locks': 100}
    fighter = PC(name="Buddy", hp=12, hp_max=12, ac_desc=4, hd=1, save=14, is_pc=True,
                 stats=Stats(16, 10, 10, 10, 10, 10), weapon="sword")
    fighter.cls = 'Fighter'
    g.party.members = [thief, fighter]
    g.torches = 3
    g.rations = 24

    g.dispatch(EnterDungeon())
    inst = getattr(g, 'dungeon_instance', None)
    assert inst is not None
    bp = getattr(inst, 'blueprint', None)
    assert bp is not None
    bpd = bp.data or {}
    edges = bpd.get('edges')
    assert isinstance(edges, list)

    # Find a locked edge.
    locked_edge = None
    for e in edges:
        if isinstance(e, dict) and e.get('locked'):
            locked_edge = e
            break
    assert locked_edge is not None, 'Expected at least one locked door edge in blueprint'
    a = int(locked_edge['a']); b = int(locked_edge['b'])

    # Ensure room A exists and identify the exit key to B.
    g.current_room_id = a
    room_a = g._ensure_room(a)
    exit_key = None
    for k, v in (room_a.get('exits') or {}).items():
        if int(v) == b:
            exit_key = str(k)
            break
    assert exit_key is not None
    assert room_a.get('doors', {}).get(exit_key) == 'locked', 'Door should be locked on side A'

    # Negative: if Open Locks is 0, picking should fail and require forcing.
    thief.thief_skills = {'Open Locks': 0}
    res0 = g.dispatch(DungeonDoorAction(exit_key=exit_key, action='pick'))
    assert res0 is not None and getattr(res0, 'status', '') != 'ok'
    thief.thief_skills = {'Open Locks': 100}

    # Pick the lock should succeed (100%) and open the door on both sides.
    res = g.dispatch(DungeonDoorAction(exit_key=exit_key, action='pick'))
    assert res is not None and getattr(res, 'status', '') == 'ok'

    room_a2 = g._ensure_room(a)
    assert room_a2.get('doors', {}).get(exit_key) == 'open'

    room_b = g._ensure_room(b)
    back_key = None
    for k, v in (room_b.get('exits') or {}).items():
        if int(v) == a:
            back_key = str(k)
            break
    assert back_key is not None
    assert room_b.get('doors', {}).get(back_key) == 'open', 'Door should be open on side B too'

    # Save/load and verify door stays open.
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'selftest_lock_save.json')
        save_game(g, path)
        g2 = Game(DummyUI(), dice_seed=12345, wilderness_seed=54321)
        g2.enable_validation = True
        load_game(g2, path)
        g2.current_room_id = a
        ra = g2._ensure_room(a)
        ek = None
        for k, v in (ra.get('exits') or {}).items():
            if int(v) == b:
                ek = str(k)
                break
        assert ek is not None
        assert ra.get('doors', {}).get(ek) == 'open', 'Door open state should persist after load'

def _test_wilderness_poi_seal_entrance(_game) -> None:
    """P6.0.0.2: interact with a wilderness dungeon entrance POI and seal it (no dungeon loop)."""

    from .game import Game
    from .wilderness import ensure_hex
    import json, hashlib

    # Choice 1: Seal/secure the entrance (15 gp)
    ui = QueueUI([1])
    g = Game(ui, dice_seed=777, wilderness_seed=888)
    g.enable_validation = True
    _mk_basic_party(g)

    g.gold = 100
    g.party_hex = (3, -1)
    hx = ensure_hex(g.world_hexes, g.party_hex, g.wilderness_rng).to_dict()

    hx["poi"] = {
        "type": "dungeon_entrance",
        "sealed": False,
        "resolved": False,
        "name": "Selftest Entrance",
    }
    k = f"{g.party_hex[0]},{g.party_hex[1]}"
    g.world_hexes[k] = hx

    before_gold = int(g.gold)
    g._handle_current_hex_poi()

    hx2 = g.world_hexes.get(k) or {}
    poi2 = hx2.get("poi") or {}
    assert bool(poi2.get("sealed")) is True, "Expected entrance to be sealed"
    assert int(g.gold) == before_gold - 15, "Expected sealing to cost 15 gp"


def _test_strict_replay_roundtrip(game) -> None:
    """P3.7: strict replay finalization.

    Records a short deterministic command sequence, then replays it in STRICT
    mode using the recorded RNG stream and verifies:
      - RNG stream matches exactly
      - AI stream matches exactly
      - final state snapshot matches
    """

    import json
    from .save_load import game_to_dict, apply_game_dict
    from .replay_runner import run_replay
    from .debug_diff import snapshot_state
    from .game import Game

    # 1) Record a tiny sequence on a fresh game with fixed seeds.
    ui = DummyUI()
    g1 = Game(ui, dice_seed=11111, wilderness_seed=22222)
    g1.enable_validation = True
    _mk_basic_party(g1)

    # Force a weekly boundary so RefreshContracts actually does work.
    g1.campaign_day = 8

    # Strip the auto-seeded faction/world state created at Game startup so this
    # test exercises wilderness generation RNG during replay.
    g1.factions = {}
    g1.conflict_clocks = {}
    g1.world_hexes = {}
    g1.last_contract_day = int(g1.campaign_day)
    g1.last_clock_day = int(g1.campaign_day)

    # Pre-seed a deterministic contract offer into the *base state* (not as a command),
    # so the replayed commands don't depend on weekly contract RNG.
    cid = "T1"
    tgt = (1, 0)  # adjacent to Town (0,0) so TravelToHex is valid
    g1.contract_offers = [
        {
            "cid": cid,
            "status": "offered",
            "title": "Test Contract",
            "desc": "Deterministic test contract.",
            "kind": "scout",
            "target_hex": [int(tgt[0]), int(tgt[1])],
            "reward_gp": 10,
            "deadline_day": int(g1.campaign_day) + 2,
        }
    ]
    g1.active_contracts = []

    # Capture the base state *before* commands, so replay can rebuild the end state.
    base_save = game_to_dict(g1)

    # Reset the replay log so the command replay is from this base state.
    from .replay import ReplayLog
    g1.replay = ReplayLog(dice_seed=g1.dice_seed, wilderness_seed=g1.wilderness_seed)

    # Reset RNG streams to their seeds so the replay log starts from a known point.
    from .rng import LoggedRandom
    from .dice import Dice
    g1.dice_rng = LoggedRandom(g1.dice_seed, channel="dice", log_fn=g1._log_rng)
    g1.wilderness_rng = LoggedRandom(g1.wilderness_seed, channel="wilderness", log_fn=g1._log_rng)
    g1.dice = Dice(rng=g1.dice_rng)
    # Rebind subsystem dice references to the active RNG logger.
    try:
        if hasattr(g1, 'treasure') and getattr(g1.treasure, 'dice', None) is not None:
            g1.treasure.dice = g1.dice
    except Exception:
        pass
    try:
        if hasattr(g1, 'encounters') and getattr(g1.encounters, 'dice', None) is not None:
            g1.encounters.dice = g1.dice
    except Exception:
        pass

    # Dispatch a small command sequence.
    from .commands import AcceptContract, TravelToHex, InvestigateCurrentLocation, ReturnToTown
    g1.dispatch(AcceptContract(cid=cid))

    # Disable random wilderness encounters to keep this as a command-layer replay test.
    g1._wilderness_encounter_check = lambda hx: None

    g1.dispatch(TravelToHex(q=int(tgt[0]), r=int(tgt[1])))
    g1.dispatch(InvestigateCurrentLocation())
    g1.dispatch(ReturnToTown())

    save = game_to_dict(g1)
    rep = save.get("replay") or {}
    # Keep replay stable: disable wilderness encounter checks (record run disabled them too).
    rep.setdefault('meta', {})
    if isinstance(rep.get('meta'), dict):
        rep['meta']['disable_wilderness_encounters'] = True
    snap1 = snapshot_state(g1)

    # 2) Apply save to a new game and replay from embedded log.
    ui2 = DummyUI()
    g2 = Game(ui2, dice_seed=99999, wilderness_seed=99999)  # will be overwritten by base_save
    g2.enable_validation = True
    apply_game_dict(g2, base_save)

    ok, msg = run_replay(g2, rep, verify_rng=True, verify_ai=True, use_recorded_rng=False)
    assert ok, f"Strict replay failed: {msg}"

    snap2 = snapshot_state(g2)
    assert snap2 == snap1, "Strict replay snapshot mismatch"


def _test_golden_replay_fixtures(game) -> None:
    """P3.8 (start): Golden replay fixtures regression wall.

    If fixtures exist, they MUST pass. If no fixtures are present, this test
    is a no-op.

    Skip by setting SKIP_GOLDEN_FIXTURES=true.
    """

    import os
    # Golden fixtures can be expensive; run only when explicitly enabled.
    if str(os.environ.get("RUN_GOLDEN_FIXTURES", "")).lower() not in ("1", "true", "yes"):
        return
    if str(os.environ.get("SKIP_GOLDEN_FIXTURES", "")).lower() in ("1", "true", "yes"):
        return

    from .fixtures_runner import default_fixtures_root, run_all_fixtures

    root = default_fixtures_root()
    ok, results = run_all_fixtures(root)
    if not results:
        return
    assert ok, "Golden fixtures failed; run scripts/run_fixtures.py for details"


def _test_poison_specials(game) -> None:
    """P2.1: poison is implemented via centralized EffectsManager and is deterministic + save/load safe."""
    from .models import Actor

    # Create a dummy monster attacker.
    m = Actor(name="Test Spider", hp=5, hp_max=5, ac_desc=7, hd=2, save=16, is_pc=False)

    # 1) Instant-death poison on failed save.
    victim = Actor(name="Victim", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save
    game._resolve_poison_special(
        m,
        victim,
        {"type": "poison", "id": "spider_bite", "mode": "instant_death", "save_tags": ["poison"]},
        round_no=1,
    )
    assert victim.hp == -1, "Expected victim to die from instant-death poison"

    # 2) DOT poison applies, ticks for N rounds, then fades.
    victim2 = Actor(name="Victim2", hp=12, hp_max=12, ac_desc=6, hd=1, save=21, is_pc=True)
    game._resolve_poison_special(
        m,
        victim2,
        {
            "type": "poison",
            "id": "wyvern_sting",
            "mode": "dot",
            "save_tags": ["poison"],
            "dot_damage": "1d1",
            "duration": "3d1",
            "save_each_tick": False,
        },
        round_no=1,
    )

    ei = getattr(victim2, "effect_instances", {}) or {}
    assert any(str(v.get("kind","")).lower() == "poison" for v in ei.values()), "DOT poison should be stored in effect_instances"

    # Tick 3 rounds: 1 damage per round, then removed.
    for rn in range(1, 4):
        game.combat_round = rn
        game._tick_combat_statuses([victim2])

    assert victim2.hp == 9, f"Expected 3 poison damage over 3 rounds, got hp={victim2.hp}"
    ei2 = getattr(victim2, "effect_instances", {}) or {}
    assert not any(str(v.get("kind","")).lower() == "poison" for v in ei2.values()), "DOT poison should have expired and been removed"


def _test_paralysis_effect(game) -> None:
    """P2.2: paralysis effect blocks actions and expires deterministically."""
    from .models import Actor

    m = Actor(name="Test Ghoul", hp=5, hp_max=5, ac_desc=7, hd=2, save=16, is_pc=False)
    victim = Actor(name="Held", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    game._resolve_paralysis_special(
        m,
        victim,
        {"type": "paralysis", "id": "ghoul_touch", "save_tags": ["paralysis"], "duration": "2d1"},
        round_no=1,
    )

    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.can_act is False, "Paralyzed target should not be able to act"

    # Tick 2 rounds then effect should expire.
    for rn in range(1, 3):
        game.combat_round = rn
        game._tick_combat_statuses([victim])

    mods2 = game.effects_mgr.query_modifiers(victim)
    assert mods2.can_act is True, "Paralysis should have expired after duration"


def _test_petrification_effect(game) -> None:
    """P2.3: petrification is a save-or-stone effect that persists."""
    from .models import Actor

    m = Actor(name="Test Cockatrice", hp=5, hp_max=5, ac_desc=7, hd=2, save=16, is_pc=False)
    victim = Actor(name="Stoned", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    game._resolve_petrification_special(
        m,
        victim,
        {"type": "petrification", "id": "cockatrice_touch", "save_tags": ["petrification"]},
        round_no=1,
    )

    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.can_act is False and mods.petrified is True, "Petrified target should be unable to act"

    # No duration: should still be petrified after multiple ticks.
    for rn in range(1, 4):
        game.combat_round = rn
        game._tick_combat_statuses([victim])
    mods2 = game.effects_mgr.query_modifiers(victim)
    assert mods2.petrified is True, "Petrification should persist (no duration)"


def _test_fear_effect(game) -> None:
    """P2.6: fear forces retreat/cowering and expires deterministically."""
    from .models import Actor

    m = Actor(name="Test Dragon", hp=30, hp_max=30, ac_desc=3, hd=8, save=10, is_pc=False)
    victim = Actor(name="Coward", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    game._resolve_fear_special(
        m,
        victim,
        {"type": "fear", "id": "dragon_fear", "save_tags": ["spells"], "duration": "2d1"},
        round_no=1,
    )

    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.forced_retreat is True, "Feared target should be forced to retreat/cower"
    assert mods.to_hit_mod <= 0, "Fear should not grant an offensive bonus"

    # Tick 2 rounds then effect should expire.
    for rn in range(1, 3):
        game.combat_round = rn
        game._tick_combat_statuses([victim])

    mods2 = game.effects_mgr.query_modifiers(victim)
    assert mods2.forced_retreat is False, "Fear should have expired after duration"


def _test_charm_effect(game) -> None:
    """P2.6: charm flips combat side for a duration."""
    from .models import Actor

    vamp = Actor(name="Test Vampire", hp=20, hp_max=20, ac_desc=4, hd=7, save=9, is_pc=False)
    victim = Actor(name="Charmed", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    game._resolve_charm_special(
        vamp,
        victim,
        {"type": "charm", "id": "vampire_charm", "save_tags": ["spells"], "duration": "2d1"},
        round_no=1,
    )

    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.side_override == "foe", "Charmed PC should be treated as foe side"

    for rn in range(1, 3):
        game.combat_round = rn
        game._tick_combat_statuses([victim])

    mods2 = game.effects_mgr.query_modifiers(victim)
    assert mods2.side_override is None, "Charm should expire and restore original allegiance"


def _test_disease_effect(game) -> None:
    """P2.7: disease persists and ticks on campaign day change; blocks healing as configured."""
    from .models import Actor
    from .effects import Hook

    mummy = Actor(name="Test Mummy", hp=20, hp_max=20, ac_desc=4, hd=5, save=12, is_pc=False)
    victim = Actor(name="Afflicted", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    game._resolve_disease_special(
        mummy,
        victim,
        {
            "type": "disease",
            "id": "mummy_rot",
            "save_tags": ["spells"],
            "damage_per_day": "1d1",
            "blocks_natural_heal": True,
            "blocks_magical_heal": True,
        },
        round_no=1,
    )

    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.blocks_natural_heal is True
    assert mods.blocks_magical_heal is True

    hp0 = victim.hp
    game.effects_mgr.tick([victim], Hook.DAY_PASSED, ctx={"day": 2, "round_no": 1})
    assert victim.hp == hp0 - 1, "Disease should deal its per-day damage"


def _test_cure_disease_spell(game) -> None:
    """P2.8: Cure Disease removes disease effects in the field."""
    from .models import Actor
    from .effects import Hook
    from .spellcasting import apply_spell_out_of_combat

    mummy = Actor(name="Test Mummy", hp=20, hp_max=20, ac_desc=4, hd=5, save=12, is_pc=False)

    # Use a real party member as the victim so the UI choose() path works.
    victim = game.party.living()[0]
    victim.save = 21  # make the save effectively impossible for deterministic testing

    game._resolve_disease_special(
        mummy,
        victim,
        {
            "type": "disease",
            "id": "mummy_rot",
            "save_tags": ["spells"],
            "damage_per_day": "1d1",
            "blocks_natural_heal": True,
            "blocks_magical_heal": True,
        },
        round_no=1,
    )

    # Ensure the disease modifiers are present before curing.
    mods = game.effects_mgr.query_modifiers(victim)
    assert mods.blocks_magical_heal is True

    # Cast Cure Disease out of combat (UI will pick the first ally = victim).
    caster = None
    for a in game.party.living():
        if str(getattr(a, "cls", "")).strip().lower() == "cleric":
            caster = a
            break
    if caster is None:
        caster = game.party.living()[0]
    apply_spell_out_of_combat(game=game, caster=caster, spell_name="Cure Disease", context="dungeon")

    mods2 = game.effects_mgr.query_modifiers(victim)
    assert mods2.blocks_magical_heal is False, "Cure Disease should remove healing block"

    # Day tick should no longer deal disease damage.
    hp0 = victim.hp
    game.effects_mgr.tick([victim], Hook.DAY_PASSED, ctx={"day": 2, "round_no": 1})
    assert victim.hp == hp0, "Cured disease should not tick damage"


def _test_spell_tag_enforcement(_: Any) -> None:
    """P3.4: spell tags are complete and within the canonical vocabulary."""

    from .data import load_json
    from .spell_tags import KNOWN_SPELL_TAGS, normalize_tags

    spells = load_json("spells.json")
    assert isinstance(spells, list)

    strict = str(os.getenv("STRICT_CONTENT", "")).strip().lower() in {"1", "true", "yes", "on"}
    try:
        min_cov = float(os.getenv("SPELL_TAG_MIN_COVERAGE", ""))
    except Exception:
        min_cov = 1.0 if strict else 0.95
    if min_cov < 0.0:
        min_cov = 0.0
    if min_cov > 1.0:
        min_cov = 1.0

    total = 0
    with_tags = 0
    unknown: list[str] = []
    for s in spells:
        if not isinstance(s, dict):
            continue
        n = s.get("name")
        if not isinstance(n, str) or not n.strip():
            continue
        total += 1
        tags = normalize_tags(s.get("tags"))
        if tags:
            with_tags += 1
        bad = [t for t in tags if t not in KNOWN_SPELL_TAGS]
        if bad:
            unknown.append(f"{n.strip()}: {bad}")

    assert total > 0
    assert (with_tags / total) >= min_cov, f"spell tag coverage {with_tags}/{total} < {min_cov}"
    assert not unknown, "unknown spell tags found: " + "; ".join(unknown[:10])


def _test_monster_ai_targeting(game) -> None:
    """P3.5: AI targeting is deterministic and respects priorities."""

    from .models import Actor
    from .monster_ai import choose_target

    fighter = Actor(name="Front", hp=12, hp_max=12, ac_desc=4, hd=1, save=14, is_pc=True)
    setattr(fighter, "cls", "fighter")
    caster = Actor(name="Caster", hp=8, hp_max=8, ac_desc=7, hd=1, save=14, is_pc=True)
    setattr(caster, "cls", "magic-user")

    cands = [fighter, caster]

    t1 = choose_target(cands, priority="caster", rng_choice_fn=game.dice_rng.choice)
    assert t1 is caster, "Expected caster priority to target the caster"

    t2 = choose_target(cands, priority="lowest_hp", rng_choice_fn=game.dice_rng.choice)
    assert t2 is caster, "Expected lowest_hp to target lowest HP"

    t3 = choose_target(cands, priority="frontline", rng_choice_fn=game.dice_rng.choice)
    assert t3 is fighter, "Expected frontline to target fighter"


def _test_damage_pipeline_gating_and_resists(game) -> None:
    """P2.9: damage typing pipeline enforces magic-weapon gating and resist/immunity/vulnerability."""
    from .models import Actor
    from .damage import DamagePacket, apply_damage_packet

    # Magic-only target.
    wraith = Actor(name="Wraith", hp=12, hp_max=12, ac_desc=4, hd=4, save=12, is_pc=False)
    wraith.damage_profile.update({"requires_magic_to_hit": True})

    hero = Actor(name="Hero", hp=10, hp_max=10, ac_desc=5, hd=1, save=14, is_pc=True, weapon="Sword")

    # Non-magical physical hit should be blocked.
    hp0 = wraith.hp
    pkt = DamagePacket(amount=5, damage_type="physical", magical=False, enchantment_bonus=0)
    dealt = apply_damage_packet(game=game, source=hero, target=wraith, packet=pkt)
    assert dealt == 0 and wraith.hp == hp0, "Non-magical weapon should not harm magic-only target"

    # +1 weapon should work.
    hero.weapon = "Sword +1"
    pkt2 = DamagePacket(amount=5, damage_type="physical", magical=True, enchantment_bonus=1)
    dealt2 = apply_damage_packet(game=game, source=hero, target=wraith, packet=pkt2)
    assert dealt2 == 5 and wraith.hp == hp0 - 5, "Magic weapon should harm magic-only target"

    # Immunity.
    imp = Actor(name="Imp", hp=10, hp_max=10, ac_desc=6, hd=2, save=14, is_pc=False)
    imp.damage_profile.update({"immunities": ["poison"]})
    hp1 = imp.hp
    pkt3 = DamagePacket(amount=6, damage_type="poison", magical=True)
    dealt3 = apply_damage_packet(game=game, source=hero, target=imp, packet=pkt3)
    assert dealt3 == 0 and imp.hp == hp1, "Poison immunity should block poison damage"

    # Resistance halves.
    salamander = Actor(name="Salamander", hp=20, hp_max=20, ac_desc=5, hd=5, save=11, is_pc=False)
    salamander.damage_profile.update({"resistances": ["fire"]})
    hp2 = salamander.hp
    pkt4 = DamagePacket(amount=9, damage_type="fire", magical=True)
    dealt4 = apply_damage_packet(game=game, source=hero, target=salamander, packet=pkt4)
    assert dealt4 == 4 and salamander.hp == hp2 - 4, "Fire resistance should halve damage (rounded down, min 1)"

    # Vulnerability doubles.
    ice_beast = Actor(name="Ice Beast", hp=20, hp_max=20, ac_desc=5, hd=5, save=11, is_pc=False)
    ice_beast.damage_profile.update({"vulnerabilities": ["fire"]})
    hp3 = ice_beast.hp
    pkt5 = DamagePacket(amount=4, damage_type="fire", magical=True)
    dealt5 = apply_damage_packet(game=game, source=hero, target=ice_beast, packet=pkt5)
    assert dealt5 == 8 and ice_beast.hp == hp3 - 8, "Fire vulnerability should double damage"

    # Transform to heal.
    golem = Actor(name="Flesh Golem", hp=10, hp_max=20, ac_desc=3, hd=8, save=10, is_pc=False)
    golem.damage_profile.update({"special_interactions": {"electric": "heal"}})
    hp4 = golem.hp
    pkt6 = DamagePacket(amount=6, damage_type="electric", magical=True)
    dealt6 = apply_damage_packet(game=game, source=hero, target=golem, packet=pkt6)
    assert dealt6 == 0 and golem.hp == min(golem.hp_max, hp4 + 6), "Electric should heal via transform"

    # Special interaction: split (Ochre Jelly divided by lightning).
    # Provide a combat_foes list so the split can spawn a new creature.
    ochre = Actor(name="Ochre Jelly", hp=12, hp_max=12, ac_desc=8, hd=5, save=11, is_pc=False)
    ochre.monster_id = "ochre_jelly"
    ochre.damage_profile.update({"special_interactions": {"electric": "split"}})
    foes = [ochre]
    pkt7 = DamagePacket(amount=7, damage_type="electric", magical=True)
    dealt7 = apply_damage_packet(game=game, source=hero, target=ochre, packet=pkt7, ctx={"combat_foes": foes})
    assert dealt7 == 0, "Split should negate damage"
    assert len(foes) == 2, "Split should add a second foe"
    assert foes[0].hp + foes[1].hp == 12, "Split should divide current HP across the two jellies"


def _test_damage_material_gating_and_magic_resistance(game) -> None:
    """P2.9+: material gating (silver) and magic resistance % for spells."""
    from .models import Actor
    from .damage import DamagePacket, apply_damage_packet
    from .spellcasting import apply_spell_in_combat

    # Material-gated target (lycanthrope needs silver).
    wolf = Actor(name="Werewolf", hp=12, hp_max=12, ac_desc=5, hd=4, save=12, is_pc=False)
    wolf.damage_profile.update({"requires_material": "silver"})

    hero = Actor(name="Hero", hp=10, hp_max=10, ac_desc=5, hd=1, save=14, is_pc=True, weapon="Sword")

    hp0 = wolf.hp
    pkt = DamagePacket(amount=5, damage_type="physical", magical=False, enchantment_bonus=0, source_tags=set())
    dealt = apply_damage_packet(game=game, source=hero, target=wolf, packet=pkt)
    assert dealt == 0 and wolf.hp == hp0, "Non-silver weapon should not harm silver-only target"

    pkt2 = DamagePacket(amount=5, damage_type="physical", magical=False, enchantment_bonus=0, source_tags={"silver"})
    dealt2 = apply_damage_packet(game=game, source=hero, target=wolf, packet=pkt2)
    assert dealt2 == 5 and wolf.hp == hp0 - 5, "Silver weapon should harm silver-only target"

    # Magic resistance: set to 100% so it always negates.
    demon = Actor(name="Demon", hp=10, hp_max=10, ac_desc=4, hd=6, save=10, is_pc=False)
    demon.damage_profile.update({"magic_resistance_pct": 100})

    # Ensure the UI chooser always targets the first foe; put demon in foes list.
    caster = None
    for a in game.party.living():
        if str(getattr(a, "cls", "")).strip().lower() == "magic-user":
            caster = a
            break
    if caster is None:
        caster = game.party.living()[0]

    hp1 = demon.hp
    apply_spell_in_combat(game=game, caster=caster, spell_name="Magic Missile", foes=[demon])
    assert demon.hp == hp1, "100% magic resistance should negate spell damage"


    # MR exceptions: Flesh golem has MR 100% but lightning bolt should still apply,
    # allowing the special_interactions transform to heal.
    flesh = Actor(name="Flesh Golem", hp=10, hp_max=20, ac_desc=3, hd=8, save=10, is_pc=False)
    flesh.damage_profile.update({
        "magic_resistance_pct": 100,
        "magic_resistance_exempt_spells": ["lightning bolt"],
        "special_interactions": {"electric": "heal"},
    })
    hp_fg = flesh.hp
    apply_spell_in_combat(game=game, caster=caster, spell_name="Lightning Bolt", foes=[flesh])
    assert flesh.hp > hp_fg, "Lightning Bolt should bypass MR via exemption and heal flesh golem"

    # Golems as spell-immune stressors: Stone golem MR 100% should negate fireball entirely.
    stone = Actor(name="Stone Golem", hp=30, hp_max=30, ac_desc=3, hd=10, save=8, is_pc=False)
    stone.damage_profile.update({"magic_resistance_pct": 100})
    hp_sg = stone.hp
    apply_spell_in_combat(game=game, caster=caster, spell_name="Fireball", foes=[stone])
    assert stone.hp == hp_sg, "Fireball should be negated by 100% MR"

    # Spell immunity (non-percentile) should also work even when MR is disabled.
    immune_stone = Actor(name="Stone Golem", hp=30, hp_max=30, ac_desc=3, hd=10, save=8, is_pc=False)
    immune_stone.damage_profile.update({"magic_resistance_pct": 0, "spell_immunity_all": True})
    hp_isg = immune_stone.hp
    apply_spell_in_combat(game=game, caster=caster, spell_name="Fireball", foes=[immune_stone])
    assert immune_stone.hp == hp_isg, "Spell immunity should block spells even when MR is 0%"

    # Category/tag immunity: block specific *classes* of spells without enumerating names.
    fire_tag_immune = Actor(name="Test Fire-Tag Immune", hp=20, hp_max=20, ac_desc=5, hd=4, save=12, is_pc=False)
    fire_tag_immune.damage_profile.update({"magic_resistance_pct": 0, "spell_immunity_tags": ["fire"]})
    hp_fti = fire_tag_immune.hp
    apply_spell_in_combat(game=game, caster=caster, spell_name="Fireball", foes=[fire_tag_immune])
    assert fire_tag_immune.hp == hp_fti, "Spell tag immunity should block fire-tagged spells (e.g., Fireball)"


def _test_energy_drain_effect(game) -> None:
    """P2.5: energy drain reduces PC level/XP and recomputes features."""
    from .game import PC
    from .models import Stats, Actor

    # Make a level 3 fighter with some XP.
    pc = PC(
        name="Drained",
        hp=18,
        hp_max=18,
        ac_desc=5,
        hd=1,
        save=14,
        is_pc=True,
        stats=Stats(14, 10, 10, 10, 10, 10),
        weapon="sword",
    )
    pc.cls = "Fighter"
    pc.level = 3
    pc.xp = 5000
    # Recompute tables for that level.
    try:
        game.recalc_pc_class_features(pc)
    except Exception:
        pass

    undead = Actor(name="Test Wight", hp=10, hp_max=10, ac_desc=5, hd=3, save=12, is_pc=False)
    old_hp_max = pc.hp_max

    game._resolve_energy_drain_special(undead, pc, {"type": "energy_drain", "id": "wight_drain", "levels": 1}, round_no=1)

    assert pc.level == 2, f"Expected level to drop to 2, got {pc.level}"
    # Fighter threshold for level 2 is 2000.
    assert pc.xp == 2000, f"Expected XP to drop to level-2 floor 2000, got {pc.xp}"
    assert pc.hp_max <= old_hp_max, "Expected hp_max to decrease or remain (should decrease)"
    assert pc.hp <= pc.hp_max, "HP must be clamped to hp_max after drain"


def _test_regeneration_effect(game) -> None:
    """P2.3: regeneration heals at the start of round."""
    from .models import Actor

    troll = Actor(name="Test Troll", hp=7, hp_max=12, ac_desc=5, hd=6, save=12, is_pc=False)
    # Apply regen as if on-spawn.
    game._apply_regeneration_spawn(troll, {"type": "regeneration", "id": "troll_regen", "amount": 3})
    assert troll.hp == 7
    game.combat_round = 1
    game._tick_combat_statuses([troll])
    assert troll.hp == 10, f"Expected troll to regen 3 hp, got {troll.hp}"
    game.combat_round = 2
    game._tick_combat_statuses([troll])
    assert troll.hp == 12, "Regen should not exceed hp_max"


def _test_regeneration_suppression(game) -> None:
    """P2.4: fire/acid damage suppresses regeneration for the next tick."""
    from .models import Actor

    troll = Actor(name="Test Troll", hp=7, hp_max=12, ac_desc=5, hd=6, save=12, is_pc=False)
    game._apply_regeneration_spawn(troll, {"type": "regeneration", "id": "troll_regen", "amount": 3})

    # Fire damage should suppress the next START_OF_ROUND heal.
    game.combat_round = 1
    game._notify_damage(troll, 1, damage_types={"fire"})
    game._tick_combat_statuses([troll])
    assert troll.hp == 7, f"Expected regen suppressed (hp stays 7), got {troll.hp}"

    # Next round regen should resume.
    game.combat_round = 2
    game._tick_combat_statuses([troll])
    assert troll.hp == 10, f"Expected regen to resume (+3), got {troll.hp}"


def _test_gaze_petrification_and_avert(game) -> None:
    """P2.4: gaze petrification pulses at start of round; avert gaze prevents it."""
    from .models import Actor

    medusa = Actor(name="Test Medusa", hp=12, hp_max=12, ac_desc=8, hd=4, save=11, is_pc=False)
    victim = Actor(name="Victim", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)  # impossible save

    # Apply gaze aura as if on-spawn.
    game.combat_round = 1
    game._apply_gaze_petrification_spawn(medusa, {"type": "gaze_petrification", "id": "medusa_gaze", "save_tags": ["petrification"]})

    # Pulse: victim should be petrified.
    game._tick_combat_statuses([victim, medusa])
    assert game.effects_mgr.query_modifiers(victim).petrified is True

    # Reset victim and test avert gaze.
    victim2 = Actor(name="Victim2", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True)
    game.combat_round = 1
    # Apply avert gaze to victim2.
    inst = {
        "kind": "avert_gaze",
        "source": {"name": victim2.name},
        "created_round": 1,
        "duration": {"type": "rounds", "remaining": 1},
        "tags": ["defensive"],
        "params": {"id": "avert_gaze", "to_hit_mod": -2},
        "state": {},
    }
    game.effects_mgr.apply(victim2, inst, ctx={"round_no": 1})
    game._tick_combat_statuses([victim2, medusa])
    assert game.effects_mgr.query_modifiers(victim2).petrified is False, "Averting gaze should prevent petrification"


def _test_dungeon_generation(game) -> None:
    # Ensure room generation works and backlinks are created.
    r1 = game._ensure_room(1)
    assert isinstance(r1, dict)
    assert "exits" in r1 and "doors" in r1
    # Generate a few rooms by following exits.
    created = {1}
    frontier = [1]
    for _ in range(15):
        if not frontier:
            break
        rid = frontier.pop(0)
        room = game._ensure_room(rid)
        for dest in (room.get("exits") or {}).values():
            if dest not in created:
                created.add(dest)
                frontier.append(dest)
                game._ensure_room(dest)
    # At least 1 room exists.
    assert len(created) >= 1
    # Backlinks: for each edge, the destination should have an exit back.
    for rid in list(created)[:5]:
        room = game._ensure_room(rid)
        for dest in (room.get("exits") or {}).values():
            back = game._ensure_room(dest)
            assert rid in (back.get("exits") or {}).values(), "missing backlink"


def _test_wilderness_poi_factions_contracts(game) -> None:
    # Ensure current hex exists
    q, r = game.party_hex
    _ = game.world_hexes
    from .wilderness import ensure_hex
    import json, hashlib

    ensure_hex(game.world_hexes, (q, r), game.wilderness_rng)

    # Force-create a POI beyond frontier.
    from .wilderness import force_poi, hex_distance

    target = (5, -2)
    hx_obj = ensure_hex(game.world_hexes, target, game.wilderness_rng)
    hk = f"{target[0]},{target[1]}"
    hx = game.world_hexes.get(hk)
    if not isinstance(hx, dict):
        hx = hx_obj.to_dict()
    dist = hex_distance(target, (0, 0))
    terrain = str(hx.get("terrain") or "clear")
    hx["poi"] = force_poi(game.wilderness_rng, dist, terrain)
    game.world_hexes[hk] = hx
    assert isinstance(game.world_hexes[hk].get("poi"), dict)

    # Contracts exist and are dicts.
    assert isinstance(game.contract_offers, list)
    if game.contract_offers:
        assert isinstance(game.contract_offers[0], dict)

    # Conflict clocks exist.
    assert isinstance(game.conflict_clocks, dict)

    # Heat field should be allowed on hexes.
    game.world_hexes[hk].setdefault("heat", 0)
    assert isinstance(game.world_hexes[hk]["heat"], int)


def _test_save_load_roundtrip(game) -> None:
    from .save_load import save_game, load_game

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "slot_test.json")
        save_game(game, path)
        assert os.path.exists(path)
        g2 = type(game)(DummyUI())
        load_game(g2, path)
        # Key invariants
        assert g2.campaign_day == game.campaign_day
        assert g2.party_hex == game.party_hex
        assert isinstance(g2.world_hexes, dict)
        assert isinstance(g2.factions, dict)


def _canonical_save_dict(d: dict) -> dict:
    """Strip volatile fields so we can detect true save/load drift."""
    if not isinstance(d, dict):
        return {}
    out = dict(d)
    # Meta is intentionally volatile (timestamps, summary strings).
    out.pop("meta", None)
    # Legacy key kept for compatibility; ignore for drift comparisons.
    out.pop("version", None)
    return out


def _test_save_load_drift_snapshot(game) -> None:
    """Save -> load -> save should be stable (ignoring metadata)."""
    import json
    from .save_load import save_game, load_game

    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "slot_a.json")
        p2 = os.path.join(td, "slot_b.json")

        save_game(game, p1)
        g2 = type(game)(DummyUI())
        load_game(g2, p1)
        save_game(g2, p2)

        a = json.load(open(p1, "r", encoding="utf-8"))
        b = json.load(open(p2, "r", encoding="utf-8"))

        ca = _canonical_save_dict(a)
        cb = _canonical_save_dict(b)
        assert ca == cb, "Save/load drift detected (excluding metadata)."


def _test_equipment_ac_sanity(game) -> None:
    """AC computed from equipment should match the helper used by char creation."""
    from .equipment import compute_ac_desc
    from .game import Game
    from .scripted_ui import ScriptedUI

    # Script: 1 PC, Fighter, weapon Sword, armor Chain, shield Yes
    actions = [
        "1",      # party size
        "ACGuy",  # name
        0,        # Fighter
        0,        # Sword
        2,        # Chain
        1,        # Shield Yes
    ]
    sui = ScriptedUI(actions=actions, auto_default=False, echo=False)
    g = Game(sui, dice_seed=777, wilderness_seed=999)
    g.enable_validation = True
    g.create_party_quick()
    pc = g.party.pcs()[0]
    dex = getattr(getattr(pc, "stats", None), "DEX", None)
    expected = compute_ac_desc(base_ac_desc=9, armor_name="Chain", shield=True, dex_score=dex)
    assert int(getattr(pc, "ac_desc", 999)) == int(expected), f"AC mismatch: got {pc.ac_desc}, expected {expected}"


def _test_level_up_xp_thresholds(game) -> None:
    """XP thresholds must be monotone and training must level PCs when conditions are met."""
    from .game import Game
    from .scripted_ui import ScriptedUI

    sui = ScriptedUI(actions=["1", "Lev", 0, 0, 0], auto_default=False, echo=False)
    g = Game(sui, dice_seed=888, wilderness_seed=999)
    g.enable_validation = True
    g.create_party_quick()
    pc = g.party.pcs()[0]

    # Monotone thresholds for first 10 levels for each core class.
    for cls in ("Fighter", "Cleric", "Thief", "Magic-User"):
        prev = -1
        for lvl in range(2, 11):
            xp = g.xp_threshold_for_class(cls, lvl)
            assert xp > prev, f"Non-increasing XP threshold for {cls} at level {lvl}: {xp} <= {prev}"
            prev = xp

    # Training path: give XP and gold; should reach level 2.
    g.gold = 10_000
    pc.xp = g.xp_threshold_for_class(getattr(pc, "cls", "Fighter"), 2)
    before_gold = g.gold
    before_hpmax = pc.hp_max
    g.train_party()
    assert pc.level >= 2, "PC did not level up during training"
    assert g.gold < before_gold, "Training did not consume gold"
    assert pc.hp_max > before_hpmax, "HP max did not increase on level up"


def _pc_to_dict(pc: Any) -> dict[str, Any]:
    """Canonical minimal snapshot for regression checks."""
    st = getattr(pc, "stats", None)
    if st is None:
        stats = None
    elif isinstance(st, dict):
        stats = {k: int(st.get(k)) for k in ("STR", "DEX", "CON", "INT", "WIS", "CHA") if k in st}
    else:
        stats = {k: int(getattr(st, k)) for k in ("STR", "DEX", "CON", "INT", "WIS", "CHA")}
    return {
        "name": getattr(pc, "name", None),
        "cls": getattr(pc, "cls", None),
        "level": int(getattr(pc, "level", 0) or 0),
        "xp": int(getattr(pc, "xp", 0) or 0),
        "hp": int(getattr(pc, "hp", 0) or 0),
        "hp_max": int(getattr(pc, "hp_max", 0) or 0),
        "ac_desc": int(getattr(pc, "ac_desc", 0) or 0),
        "save": int(getattr(pc, "save", 0) or 0),
        "weapon": getattr(pc, "weapon", None),
        "armor": getattr(pc, "armor", None),
        "shield": bool(getattr(pc, "shield", False)),
        "stats": stats,
    }


def _create_one_pc(*, cls_index: int, dice_seed: int = 1234) -> dict[str, Any]:
    """Create a single PC via the real character creation flow, deterministically."""
    from .game import Game
    from .scripted_ui import ScriptedUI

    # Script: party size=1, name, class, weapon, armor (None) => no shield prompt.
    actions = [
        "1",          # party size
        "Alice",      # name
        cls_index,     # class
        0,            # weapon: Sword
        0,            # armor: None
    ]
    sui = ScriptedUI(actions=actions, auto_default=False, echo=False)
    g = Game(sui, dice_seed=dice_seed, wilderness_seed=999)
    g.enable_validation = True
    g.create_party_quick()
    pcs = g.party.pcs()
    assert len(pcs) == 1, "Expected exactly one PC"
    return _pc_to_dict(pcs[0])


def _test_character_creation_determinism(game) -> None:
    # Same seed + same inputs => identical created character snapshot.
    a = _create_one_pc(cls_index=0, dice_seed=424242)
    b = _create_one_pc(cls_index=0, dice_seed=424242)
    assert a == b, f"Character creation is not deterministic.\nA={a}\nB={b}"


def _test_character_creation_class_smoke(game) -> None:
    # One golden-path create per class; validate that the package is initialized.
    classes = ["Fighter", "Cleric", "Thief", "Magic-User"]
    for idx, cname in enumerate(classes):
        snap = _create_one_pc(cls_index=idx, dice_seed=1000 + idx)
        assert snap["cls"] == cname
        assert snap["level"] == 1
        assert snap["hp_max"] >= 1
        assert snap["stats"] is not None
        for k in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
            v = snap["stats"][k]
            assert 3 <= v <= 18
        # Weapon should always be initialized in this flow.
        assert isinstance(snap["weapon"], str) and snap["weapon"]


def _test_turn_undead_combat(game) -> None:
    """Cleric should be able to turn undead and end the fight via fleeing."""
    from .game import Game, PC
    from .models import Stats, Actor
    from .scripted_ui import ScriptedUI

    # Choose "Turn Undead" when prompted.
    # First prompt chooses "Turn Undead"; subsequent prompts can default.
    sui = ScriptedUI(actions=[1], auto_default=True, echo=False)
    sui.auto_combat = False
    g = Game(sui, dice_seed=111, wilderness_seed=222)
    g.enable_validation = True

    cleric = PC(name="Cleric", hp=6, hp_max=6, ac_desc=7, hd=1, save=14, is_pc=True,
                cls="Cleric", level=1, stats=Stats(10, 10, 10, 10, 14, 10), weapon="mace")
    g.party.members = [cleric]
    g.light_on = True

    skel = Actor(name="Skeleton", hp=4, hp_max=4, ac_desc=7, hd=1, save=15, morale=None, tags=['undead'])
    g.combat([skel])
    assert ('turned' in skel.effects) or ('fled' in skel.effects) or (skel.hp <= 0), "Skeleton was not affected by turning"


def _test_thief_lockpick(game) -> None:
    """Locked doors should be pickable if a thief is present."""
    from .game import Game, PC
    from .models import Stats
    from .scripted_ui import ScriptedUI

    sui = ScriptedUI(actions=[], auto_default=True, echo=False)
    g = Game(sui, dice_seed=333, wilderness_seed=444)
    g.enable_validation = True

    thief = PC(name="Thief", hp=5, hp_max=5, ac_desc=7, hd=1, save=14, is_pc=True,
               cls="Thief", level=1, stats=Stats(10, 14, 10, 10, 10, 10), weapon="dagger")
    # Make lockpick effectively guaranteed.
    thief.thief_skills = {'Open Locks': 99}
    g.party.members = [thief]

    g.current_room_id = 1
    room = g._ensure_room(1)
    # Ensure a locked exit exists.
    k = next(iter(room['exits'].keys()))
    room['doors'][k] = 'locked'
    res = g._cmd_dungeon_door_action(k, 'pick')
    assert getattr(res, 'status', None) == 'ok', f"Lockpick failed unexpectedly: {res}"
    assert room['doors'][k] == 'open', "Door did not open after lockpick"


def _test_secret_edges_hidden_and_revealed(game) -> None:
    """Secret edges should be hidden until found, then persist after save/load."""
    import os, tempfile
    from .game import Game, PC
    from .models import Stats
    from .scripted_ui import ScriptedUI
    from .save_load import save_game, load_game

    sui = ScriptedUI(actions=[], auto_default=True, echo=False)
    g1 = Game(sui, dice_seed=777, wilderness_seed=888)
    g1.enable_validation = True

    # Basic party; race/class doesn't matter because we force the search roll.
    pc = PC(name="Scout", hp=6, hp_max=6, ac_desc=7, hd=1, save=14, is_pc=True,
            cls="Fighter", level=1, stats=Stats(10, 10, 10, 10, 10, 10), weapon="sword")
    pc.race = "Elf"  # harmless; active search odds come from races module anyway
    g1.party.members = [pc]

    # Enter dungeon to ensure instance exists.
    g1._cmd_enter_dungeon()
    inst = g1.dungeon_instance
    assert inst is not None, "Expected dungeon_instance to exist after entering dungeon"
    bp = inst.blueprint.data
    edges = bp.get("edges") or []
    rooms = bp.get("rooms") or {}

    # Pick a candidate edge and force it to be secret for deterministic testing.
    cand = None
    for e in edges:
        if not isinstance(e, dict):
            continue
        a = int(e.get("a", 0)); b = int(e.get("b", 0))
        if a <= 0 or b <= 0:
            continue
        if a == 1 or b == 1:
            continue
        ra = rooms.get(a) or {}
        rb = rooms.get(b) or {}
        if int(ra.get("floor", 0)) != int(rb.get("floor", 0)):
            continue
        # Avoid stairs edges by tag as well
        if "stairs" in " ".join((ra.get("tags") or []) + (rb.get("tags") or [])).lower():
            continue
        cand = (a, b, e)
        break
    assert cand is not None, "No suitable edge found to mark secret"
    a, b, e = cand
    e["secret"] = True
    e["locked"] = False

    # Ensure not already discovered.
    inst.delta.data.setdefault("secret_edges_found", {}).pop(g1._dungeon_secret_edge_key(a, b), None)

    g1.current_room_id = int(a)
    room_before = g1._ensure_room(int(a))
    assert int(b) not in set(room_before.get("exits", {}).values()), "Secret edge should be hidden before discovery"

    # Force search success.
    old_in6 = g1.dice.in_6
    g1.dice.in_6 = lambda n: True
    try:
        g1._cmd_dungeon_search_secret()
    finally:
        g1.dice.in_6 = old_in6

    room_after = g1._ensure_room(int(a))
    assert int(b) in set(room_after.get("exits", {}).values()), "Secret edge should be visible after discovery"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "secret_edge_save.json")
        save_game(g1, path)

        g2 = Game(ScriptedUI(actions=[], auto_default=True, echo=False), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        g2.party.members = [pc]
        load_game(g2, path)

        g2.current_room_id = int(a)
        room_loaded = g2._ensure_room(int(a))
        assert int(b) in set(room_loaded.get("exits", {}).values()), "Secret edge should remain visible after load"



def _test_dungeon_ecology_alarm_and_save_load(_game) -> None:
    """P6.1.4.81: ecology/alarm/clues should persist across save/load mid-dungeon."""
    from .game import Game
    from .save_load import save_game, load_game

    ui = DummyUI()
    g1 = Game(ui, dice_seed=5151, wilderness_seed=6161)
    g1.enable_validation = True
    _mk_basic_party(g1)
    g1._cmd_enter_dungeon()

    lvl = int(getattr(g1, 'dungeon_level', 1) or 1)
    g1.current_room_id = 1
    g1.noise_level = 4
    g1.dungeon_alarm_by_level[lvl] = 3
    g1._record_dungeon_clue('Test clue from selftest.', source='selftest', room_id=1, level=lvl)
    g1._ensure_dungeon_ecology_groups(lvl)
    assert getattr(g1, 'dungeon_monster_groups', None), 'Expected ecology groups to exist'

    grp = [g for g in (g1.dungeon_monster_groups or []) if int(g.get('level', 0) or 0) == lvl][0]
    grp['room_id'] = int(g1.current_room_id)
    gid = int(grp.get('gid', 0) or 0)
    g1.pending_dungeon_ecology_group_ids = [gid]
    before_alarm = dict(getattr(g1, 'dungeon_alarm_by_level', {}) or {})
    before_groups = [dict(g) for g in (g1.dungeon_monster_groups or [])]
    before_pending = list(getattr(g1, 'pending_dungeon_ecology_group_ids', []) or [])
    before_clues = list(getattr(g1, 'dungeon_clues', []) or [])
    hud = '\n'.join(g1._dungeon_hud_lines())
    assert 'Alarm 3/5' in hud, 'Dungeon HUD should show current level alarm'

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'ecology_alarm_save.json')
        save_game(g1, path)
        g2 = Game(DummyUI(), dice_seed=1, wilderness_seed=1)
        g2.enable_validation = True
        _mk_basic_party(g2)
        load_game(g2, path)

        assert dict(getattr(g2, 'dungeon_alarm_by_level', {}) or {}) == before_alarm, 'Alarm by level should persist'
        assert list(getattr(g2, 'pending_dungeon_ecology_group_ids', []) or []) == before_pending, 'Pending ecology contacts should persist'
        assert list(getattr(g2, 'dungeon_clues', []) or []) == before_clues, 'Dungeon clues should persist'
        assert list(getattr(g2, 'dungeon_monster_groups', []) or []) == before_groups, 'Dungeon ecology groups should persist'
        hud2 = '\n'.join(g2._dungeon_hud_lines())
        assert 'Alarm 3/5' in hud2, 'Loaded dungeon HUD should show persisted alarm'

def _test_magic_user_cast_sleep(game) -> None:
    """Magic-User should be able to cast Sleep and apply the asleep effect."""
    from .game import Game, PC
    from .models import Stats, Actor
    from .scripted_ui import ScriptedUI

    # choose first spell (Sleep)
    sui = ScriptedUI(actions=[0], auto_default=False, echo=False)
    sui.auto_combat = False
    g = Game(sui, dice_seed=555, wilderness_seed=666)

    mu = PC(name="MU", hp=4, hp_max=4, ac_desc=9, hd=1, save=15, is_pc=True,
            cls="Magic-User", level=1, stats=Stats(8, 10, 10, 14, 10, 10), weapon="dagger")
    mu.spells_prepared = ['Sleep']
    g.party.members = [mu]

    foes = [Actor(name=f"Goblin {i}", hp=3, hp_max=3, ac_desc=6, hd=1, save=15, morale=7, tags=['humanoid']) for i in range(3)]
    g._cast_spell(mu, foes)
    assert 'Sleep' not in mu.spells_prepared, "Spell was not consumed"


def _test_spell_prepare_and_status_persistence(game) -> None:
    """Spell preparation respects slots and Actor.status persists through save/load."""
    from .save_load import game_to_dict, apply_game_dict
    from .game import Game, PC
    from .models import Stats
    from .scripted_ui import ScriptedUI

    # Use a scripted UI for deterministic choices.
    sui = ScriptedUI(actions=[0, 0], auto_default=False, echo=False)
    g = Game(sui, dice_seed=777, wilderness_seed=888)

    mu = PC(name="Test MU", hp=4, hp_max=4, ac_desc=9, hd=1, save=15, is_pc=True,
            cls="Magic-User", level=1, stats=Stats(8, 10, 10, 14, 10, 10), weapon="dagger")
    g.party.members = [mu]

    mu.spells_known = ['Sleep', 'Magic Missile']
    mu.spell_slots = {1: 1}

    # prepare_spells_menu: scripted choices already queued.
    g.prepare_spells_menu(in_dungeon=False)
    assert 0 < len(mu.spells_prepared) <= 1, "Prepared spell count outside slots"

    # Status persistence
    mu.status['bless'] = {'rounds': 2, 'to_hit': 1}
    d = game_to_dict(g)
    game2 = Game(sui, dice_seed=123, wilderness_seed=456)
    apply_game_dict(game2, d)
    mu2 = next((pc for pc in game2.party.pcs() if pc.name == mu.name), None)
    assert mu2 is not None, "Missing MU after load"
    assert isinstance(mu2.status, dict) and 'bless' in mu2.status, "Actor.status not persisted"


def _test_dungeon_cast_light_spell(game) -> None:
    """Casting Light in the dungeon should create magical light turns."""
    from .game import Game, PC
    from .models import Stats
    from .scripted_ui import ScriptedUI

    # Choose caster 0, then spell 0.
    sui = ScriptedUI(actions=[0, 0], auto_default=True, echo=False)
    g = Game(sui, dice_seed=777, wilderness_seed=888)

    mu = PC(name="MU", hp=4, hp_max=4, ac_desc=9, hd=1, save=15, is_pc=True,
            cls="Magic-User", level=1, stats=Stats(8, 10, 10, 14, 10, 10), weapon="dagger")
    mu.spells_prepared = ["Light"]
    g.party.members = [mu]
    g.current_room_id = 1
    g._ensure_room(1)

    res = g._cmd_dungeon_cast_spell()
    assert getattr(res, 'status', None) == 'ok', "Dungeon cast spell failed"
    assert int(getattr(g, 'magical_light_turns', 0) or 0) > 0, "Light did not set magical_light_turns"
    assert g.light_on, "Game did not become lit after Light"


def _test_thief_skill_table_values(game) -> None:
    """Thief skill table should match expected level 1 values."""
    from .classes import thief_skills_for_level

    s = thief_skills_for_level(1)
    assert s["Open Locks"] == 10
    assert s["Find/Remove Traps"] == 15
    assert s["Hear Noise"] == 3




def _test_scripted_playthrough() -> None:
    """
    Scripted UI driver that walks through a tiny "real play" slice:

    Gather Rumors -> Accept first contract -> Travel to target hex (wilderness loop)
    -> Investigate if POI-targeted -> Return to Town.

    This catches regressions across menus and loop glue without human input.
    """
    from .game import Game
    from .scripted_ui import ScriptedUI
    from .wilderness import neighbors, hex_distance

    sui = ScriptedUI(actions=[], auto_default=True, echo=False)
    g = Game(sui)
    g.enable_validation = True
    g.ui.auto_combat = True

    # Create a party using defaults (scripted UI auto-accepts defaults)
    g.create_party_quick()
    g.gold = 200
    g.rations = 48
    g.torches = 5

    # Ensure factions and contracts exist
    g._init_factions_if_needed()
    g._refresh_contracts_if_needed()
    # Prefer a nearby contract so scripted travel finishes quickly.
    try:
        from .wilderness import hex_distance
        g.contract_offers.sort(key=lambda c: hex_distance((0,0), (int((c.get('target_hex') or [0,0])[0]), int((c.get('target_hex') or [0,0])[1]))))
    except Exception:
        pass

    # Disable wilderness encounters for this scripted navigation test.
    g._wilderness_encounter_check = lambda hx: None

    # 1) Gather a rumor (town action)
    g.gather_rumors()

    # 2) Accept first contract through the contracts UI (scripted choices)
    # Contracts menu: View Offers -> pick first -> Back
    g.ui.push(0, 0, 2)
    g.view_contracts()

    # Determine target from accepted contract
    active = [c for c in (g.active_contracts or []) if c.get("status") == "accepted"]
    assert active, "No contract accepted in scripted flow"
    target_hex = tuple(active[0].get("target_hex") or [0, 0])

        # 3) Navigate to the contract target and return using the scripted runner (bounded steps)
    from .scripted_runner import run_contract_navigation
    run_contract_navigation(g, max_steps=60)
    assert tuple(g.party_hex) == (0, 0), "Expected to return to Town (0,0)"



def _test_combat_stress_monsters(game) -> None:
    """P3.1+: deterministic-ish auto-combat smoke tests against 'stress' monsters.

    These are not outcome assertions; they ensure the full combat loop can run
    with damage profiles + specials for key archetypes without crashing.
    """
    game.light_on = True

    foe_names = [
        "Troll",
        "Flesh Golem",
        "Ghost",
        "Lich",
        "Demon, Dretch",
        "Dragon",
    ]

    foes = []
    for nm in foe_names:
        md = next((m for m in game.content.monsters if m.get("name") == nm), None)
        assert md is not None, f"Missing monster in content: {nm}"
        foes.append(game.encounters._mk_monster(md))

    for foe in foes:
        for pc in game.party.members:
            pc.hp = pc.hp_max
        game.combat([foe])
        assert isinstance(foe.hp, int)


def _test_grapple_engulf_swallow(game) -> None:
    """P3.3: grapple/engulf/swallow effects are applied, tick, and can clear."""
    from .models import Actor, Stats
    from .game import PC

    attacker = Actor(name="Test Constrictor", hp=20, hp_max=20, ac_desc=5, hd=4, save=12, is_pc=False)
    # Grapple: applies, deals optional damage, persists until Escape action succeeds.
    victim = PC(name="Grappled", hp=12, hp_max=12, ac_desc=6, hd=1, save=21, is_pc=True, stats=Stats(16, 10, 10, 10, 10, 10), weapon="sword")
    game._resolve_grapple_special(
        attacker,
        victim,
        {
            "type": "grapple",
            "id": "test_grapple",
            "save_tags": ["paralysis"],
            "save_on_apply": False,
            "escape_dc": 30,
            "escape_bonus": 25,
            "damage_per_round": "1d1",
            "damage_type": "physical",
        },
        round_no=1,
    )

    ei = getattr(victim, "effect_instances", {}) or {}
    assert any(str(v.get("kind", "")).lower() == "grapple" for v in ei.values()), "Grapple effect should be present"

    hp0 = victim.hp
    game.combat_round = 1
    game._tick_combat_statuses([victim])

    # Took 1 damage; should still be grappled (auto-escape disabled in P3.3.1).
    assert victim.hp == hp0 - 1, "Expected grapple constrict damage to apply"
    ei2 = getattr(victim, "effect_instances", {}) or {}
    assert any(str(v.get("kind", "")).lower() == "grapple" for v in ei2.values()), "Expected grapple to persist until Escape action"

    game._resolve_escape_action(victim, round_no=1)
    ei3 = getattr(victim, "effect_instances", {}) or {}
    assert not any(str(v.get("kind", "")).lower() == "grapple" for v in ei3.values()), "Expected grapple to be removed after Escape action"
    # Engulf: blocks actions and deals damage; persists until Escape action succeeds.
    victim2 = PC(name="Engulfed", hp=10, hp_max=10, ac_desc=6, hd=1, save=21, is_pc=True, stats=Stats(16, 10, 10, 10, 10, 10), weapon="sword")
    game._resolve_engulf_special(
        attacker,
        victim2,
        {
            "type": "engulf",
            "id": "test_engulf",
            "save_tags": ["paralysis"],
            "save_on_apply": False,
            "escape_dc": 30,
            "escape_bonus": 25,
            "damage_per_round": "1d1",
            "damage_type": "acid",
        },
        round_no=1,
    )
    mods = game.effects_mgr.query_modifiers(victim2)
    assert mods.can_act is False, "Engulfed target should not be able to act"

    hp1 = victim2.hp
    game.combat_round = 1
    game._tick_combat_statuses([victim2])
    assert victim2.hp == hp1 - 1, "Expected engulf damage to apply"
    mods2 = game.effects_mgr.query_modifiers(victim2)
    assert mods2.can_act is False, "Expected engulf to persist until Escape action"

    game._resolve_escape_action(victim2, round_no=1)
    mods3 = game.effects_mgr.query_modifiers(victim2)
    assert mods3.can_act is True, "Expected engulf to be removed after Escape action"


    # Swallow: blocks actions and deals digestion damage each round; can be escaped by cutting out.
    victim3 = PC(name="Swallowed", hp=12, hp_max=12, ac_desc=6, hd=1, save=21, is_pc=True, stats=Stats(16, 10, 10, 10, 10, 10), weapon="dagger")
    game._resolve_swallow_special(
        attacker,
        victim3,
        {
            "type": "swallow",
            "id": "test_swallow",
            "min_attack_roll": 1,
            "save_on_apply": False,
            "damage_per_round": "1d1",
            "damage_type": "acid",
            "cut_out_damage": 1,
        },
        round_no=1,
        attack_roll=20,
    )
    mods3 = game.effects_mgr.query_modifiers(victim3)
    assert mods3.can_act is False, 'Swallowed target should not be able to act'

    hp2 = victim3.hp
    game.combat_round = 1
    game._tick_combat_statuses([victim3])
    assert victim3.hp == hp2 - 1, 'Expected swallow digestion damage to apply'

    # From inside: one successful hit should cut out (threshold=1).
    # Provide combat_foes list containing the swallower for lookup.
    game._resolve_inside_attack(victim3, round_no=1, combat_foes=[attacker])
    ei4 = getattr(victim3, "effect_instances", {}) or {}
    assert not any(str(v.get("kind", "")).lower() == "swallow" for v in ei4.values()), "Expected swallow to be removed after cutting out"




def _test_content_lint(game) -> None:
    """Semantic content checks; strict mode fails warnings when STRICT_CONTENT is set."""
    from .content_lint import lint_content
    strict_env = (os.environ.get("STRICT_CONTENT", "").strip().lower() == "true")
    issues = lint_content(strict=bool(strict_env))
    if not issues:
        return
    # Always fail on errors; fail on warnings only in strict.
    errors = [i for i in issues if i.severity == "ERROR"]
    warns = [i for i in issues if i.severity != "ERROR"]
    if errors:
        raise AssertionError(f"Content lint errors: {len(errors)} (see scripts/content_lint.py for details).")
    if strict_env and warns:
        raise AssertionError(f"Content lint warnings in STRICT_CONTENT: {len(warns)} (see scripts/content_lint.py).")


def _test_attribute_bonus_tables(game) -> None:
    """Verify key attribute modifier breakpoints from S&W Complete."""

    from .attribute_rules import (
        strength_mods,
        dexterity_mods,
        constitution_mods,
        max_special_hirelings,
        cleric_bonus_first_level_spell,
        xp_bonus_percent,
    )
    from .models import Stats

    # Strength: penalties apply to everyone; bonuses are Fighter-only by default.
    s4_f = strength_mods(4, "Fighter")
    s4_c = strength_mods(4, "Cleric")
    assert (s4_f.to_hit, s4_f.dmg) == (-2, -1)
    assert (s4_c.to_hit, s4_c.dmg) == (-2, -1)

    s16_f = strength_mods(16, "Fighter")
    s16_c = strength_mods(16, "Cleric")
    assert (s16_f.to_hit, s16_f.dmg) == (1, 1)
    assert (s16_c.to_hit, s16_c.dmg) == (0, 0)

    s18_f = strength_mods(18, "Fighter")
    assert (s18_f.to_hit, s18_f.dmg) == (2, 3)

    # Dexterity: 3-8 => missile -1, AC worse by 1 (descending); 13+ => missile +1, AC better by 1.
    d8 = dexterity_mods(8)
    d9 = dexterity_mods(9)
    d13 = dexterity_mods(13)
    assert (d8.missile_to_hit, d8.ac_desc_mod) == (-1, +1)
    assert (d9.missile_to_hit, d9.ac_desc_mod) == (0, 0)
    assert (d13.missile_to_hit, d13.ac_desc_mod) == (+1, -1)

    # Constitution: 3-8 => -1 hp/HD, 50%; 9-12 => 0, 75%; 13+ => +1 hp/HD, 100%
    c8 = constitution_mods(8)
    c9 = constitution_mods(9)
    c13 = constitution_mods(13)
    assert (c8.hp_per_hd, c8.survive_raise_dead_pct) == (-1, 50)
    assert (c9.hp_per_hd, c9.survive_raise_dead_pct) == (0, 75)
    assert (c13.hp_per_hd, c13.survive_raise_dead_pct) == (+1, 100)

    # Charisma: special hirelings caps
    assert max_special_hirelings(3) == 1
    assert max_special_hirelings(9) == 4
    assert max_special_hirelings(18) == 7

    # Cleric wisdom bonus spell: 15+ => +1 first level spell.
    assert cleric_bonus_first_level_spell(14) == 0
    assert cleric_bonus_first_level_spell(15) == 1

    # XP bonus stacking: prime 13+ (+5) plus Wis 13+ (+5) plus Cha 13+ (+5)
    # Fighter prime = STR.
    st = Stats(13, 10, 10, 10, 13, 13)
    assert xp_bonus_percent("Fighter", st) == 15
    st2 = Stats(12, 10, 10, 10, 13, 13)
    assert xp_bonus_percent("Fighter", st2) == 10  # no prime bonus


def run_selftest(*, seed: int | None = None, fast: bool = False) -> Tuple[bool, str]:
    """Run tests and return (ok, report)."""

    from .game import Game

    ui = DummyUI()
    base = int(seed) if seed is not None else 12345
    g = Game(ui, dice_seed=base, wilderness_seed=base + 11111)
    g.enable_validation = True

    _mk_basic_party(g)

    tests = [
        ("town_shop_purchase_and_save_load", _test_town_shop_purchase_and_save_load),
        ("save_schema_error_paths", _test_save_schema_error_paths),
        ("determinism_gate_same_seed", _test_determinism_gate_same_seed),
        ("retainers_and_contracts_no_dup_save_load", _test_retainers_and_contracts_no_dup_save_load),
        ("dungeon_enter_and_save_load", _test_dungeon_enter_and_save_load),
        ("dungeon_stairs_travel_and_persist", _test_dungeon_stairs_travel_and_persist),
        ("dungeon_traps_find_disarm_and_persist", _test_dungeon_traps_find_disarm_and_persist),
        ("dungeon_listen_at_closed_door", _test_dungeon_listen_at_closed_door),
        ("dungeon_pick_lock_requires_skill_and_persists", _test_dungeon_pick_lock_requires_skill_and_persists),
        ("secret_edges_hidden_and_revealed", _test_secret_edges_hidden_and_revealed),
        ("wilderness_poi_seal_entrance", _test_wilderness_poi_seal_entrance),
        ("character_creation_determinism", _test_character_creation_determinism),
        ("character_creation_class_smoke", _test_character_creation_class_smoke),
        ("attribute_bonus_tables", _test_attribute_bonus_tables),
        ("turn_undead_combat", _test_turn_undead_combat),
        ("thief_lockpick", _test_thief_lockpick),
        ("dungeon_ecology_alarm_and_save_load", _test_dungeon_ecology_alarm_and_save_load),
        ("magic_user_cast_sleep", _test_magic_user_cast_sleep),
        ("spell_prepare_and_status_persistence", _test_spell_prepare_and_status_persistence),
        ("dungeon_cast_light_spell", _test_dungeon_cast_light_spell),
        ("thief_skill_table_values", _test_thief_skill_table_values),
        ("save_load_drift_snapshot", _test_save_load_drift_snapshot),
        ("equipment_ac_sanity", _test_equipment_ac_sanity),
        ("level_up_xp_thresholds", _test_level_up_xp_thresholds),
        ("poison_specials", _test_poison_specials),
        ("paralysis_effect", _test_paralysis_effect),
        ("petrification_effect", _test_petrification_effect),
        ("fear_effect", _test_fear_effect),
        ("charm_effect", _test_charm_effect),
        ("disease_effect", _test_disease_effect),
        ("grapple_engulf_swallow", _test_grapple_engulf_swallow),
        ("cure_disease_spell", _test_cure_disease_spell),
        ("spell_tag_enforcement", _test_spell_tag_enforcement),
        ("monster_ai_targeting", _test_monster_ai_targeting),
        ("damage_pipeline_gating_and_resists", _test_damage_pipeline_gating_and_resists),
        ("damage_material_gating_and_magic_resistance", _test_damage_material_gating_and_magic_resistance),
        ("energy_drain_effect", _test_energy_drain_effect),
        ("regeneration_effect", _test_regeneration_effect),
        ("regeneration_suppression", _test_regeneration_suppression),
        ("gaze_petrification_avert", _test_gaze_petrification_and_avert),
        ("combat", _test_combat),
        ("strict_replay_roundtrip", _test_strict_replay_roundtrip),
        ("golden_replay_fixtures", _test_golden_replay_fixtures),
        ("dungeon_generation", _test_dungeon_generation),
        ("wilderness_poi_factions_contracts", _test_wilderness_poi_factions_contracts),
        ("save_load_roundtrip", _test_save_load_roundtrip),
        ("scripted_playthrough", lambda game: _test_scripted_playthrough()),
    ]

    passed = 0
    failed: list[str] = []
    from .debug_diff import snapshot_state, diff_snapshots

    if fast:
        tests = tests[:3]

    for name, fn in tests:
        before = snapshot_state(g)
        try:
            fn(g)
            passed += 1
        except Exception as e:
            after = snapshot_state(g)
            diffs = diff_snapshots(before, after)
            diff_txt = ""
            if diffs:
                # Keep output compact.
                diff_lines = [f"    - {d['field']}: {d['before']} -> {d['after']}" for d in diffs[:12]]
                if len(diffs) > 12:
                    diff_lines.append(f"    ... ({len(diffs) - 12} more)")
                diff_txt = "\n  State diff:\n" + "\n".join(diff_lines)
            failed.append(f"- {name}: {type(e).__name__}: {e}{diff_txt}")

    ok = len(failed) == 0
    lines = [
        "SELFTEST RESULTS",
        f"Passed: {passed}/{len(tests)}",
    ]
    if failed:
        lines.append("Failures:")
        lines.extend(failed)
    else:
        lines.append("All tests passed.")
    return ok, "\n".join(lines)
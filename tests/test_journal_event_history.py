from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX


CANONICAL_POI_ID = "poi:canonical:dungeon_entrance"


def _new_game(seed: int = 2000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_discovery_emits_canonical_event_and_projects_journal():
    g = _new_game()
    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok

    rows = [e for e in (g.event_history or []) if str(e.get("type") or "") == "discovery.recorded"]
    assert len(rows) >= 1
    d = rows[-1]
    assert int(d.get("eid", -1)) >= 0
    assert str((d.get("payload") or {}).get("name") or "")

    discoveries = g._journal_discoveries()
    assert discoveries
    last = discoveries[-1]
    assert (int(last.get("q", 0)), int(last.get("r", 0))) == tuple(DUNGEON_ENTRANCE_HEX)


def test_rumor_and_clue_events_persist_across_save_load():
    g = _new_game(seed=2100)
    g._record_dungeon_clue("A chalk arrow points east.", source="test", room_id=1, level=1)
    before_rumors = len(g._journal_rumors())
    g.gather_rumors()
    assert len(g._journal_rumors()) >= before_rumors

    data = game_to_dict(g)
    g2 = _new_game(seed=2101)
    apply_game_dict(g2, data)

    types = [str(e.get("type") or "") for e in (g2.event_history or [])]
    assert "rumor.learned" in types
    assert "clue.found" in types
    eids = [int(e.get("eid", -1)) for e in (g2.event_history or [])]
    assert eids == sorted(eids)
    assert int(getattr(g2, "next_event_eid", 0)) == (max(eids) + 1 if eids else 0)
    assert g2._journal_rumors()
    assert g2._journal_clues()


def test_old_save_journal_migrates_to_event_history_once_without_duplication():
    legacy = game_to_dict(_new_game(seed=2200))
    legacy.pop("events", None)
    legacy["save_version"] = 10
    legacy["version"] = 10
    legacy["journal"] = {
        "discoveries": [
            {
                "day": 2,
                "watch": 1,
                "q": DUNGEON_ENTRANCE_HEX[0],
                "r": DUNGEON_ENTRANCE_HEX[1],
                "terrain": "hills",
                "kind": "dungeon_entrance",
                "name": "Ancient Stairs",
                "note": "Old marks",
            }
        ],
        "rumors": [
            {
                "day": 2,
                "q": DUNGEON_ENTRANCE_HEX[0],
                "r": DUNGEON_ENTRANCE_HEX[1],
                "terrain": "hills",
                "kind": "dungeon_entrance",
                "hint": "An old way down.",
                "poi_id": CANONICAL_POI_ID,
                "seen": False,
            }
        ],
        "dungeon_clues": [
            {"day": 2, "watch": 1, "level": 1, "room_id": 1, "text": "Bones near the arch.", "source": "legacy"}
        ],
        "district_notes": [
            {"day": 2, "watch": 1, "cid": "C-1", "text": "Survey contract active."}
        ],
    }

    g = _new_game(seed=2201)
    apply_game_dict(g, legacy)
    assert len(g.event_history) >= 4
    migrated_eids = [int(e.get("eid", -1)) for e in (g.event_history or [])]
    assert migrated_eids == sorted(migrated_eids)
    assert int(getattr(g, "next_event_eid", 0)) == (max(migrated_eids) + 1 if migrated_eids else 0)

    out = game_to_dict(g)
    g2 = _new_game(seed=2202)
    apply_game_dict(g2, out)
    assert len([e for e in (g2.event_history or []) if str((e.get("payload") or {}).get("text") or "") == "Bones near the arch."]) == 1
    assert len(g2._journal_discoveries()) == 1
    assert len(g2._journal_rumors()) >= 1
    assert len(g2._journal_clues()) == 1


def test_old_event_history_without_eids_gets_deterministic_ids_on_load():
    g = _new_game(seed=2300)
    g.gather_rumors()
    payload = game_to_dict(g)
    payload["events"]["event_history"] = [
        {k: v for k, v in e.items() if k != "eid"}
        for e in (payload.get("events", {}).get("event_history", []) or [])
    ]
    payload["events"].pop("next_eid", None)

    g2 = _new_game(seed=2301)
    apply_game_dict(g2, payload)

    eids = [int(e.get("eid", -1)) for e in (g2.event_history or [])]
    assert eids == list(range(len(eids)))
    assert int(getattr(g2, "next_event_eid", 0)) == len(eids)


def test_load_normalizes_out_of_order_or_duplicate_eids_to_strict_monotonic_sequence():
    g = _new_game(seed=2400)
    g.gather_rumors()
    payload = game_to_dict(g)
    hist = list(payload.get("events", {}).get("event_history", []) or [])
    if len(hist) < 2:
        raise AssertionError("expected at least two events for normalization test")

    # Corrupt to non-monotonic/duplicate ids.
    hist[0]["eid"] = 5
    hist[1]["eid"] = 5
    payload["events"]["event_history"] = list(reversed(hist))
    payload["events"]["next_eid"] = 1

    g2 = _new_game(seed=2401)
    apply_game_dict(g2, payload)

    eids = [int(e.get("eid", -1)) for e in (g2.event_history or [])]
    assert eids == list(range(len(eids)))
    assert int(getattr(g2, "next_event_eid", 0)) == len(eids)


def _mk_event(eid, etype="rumor.learned", payload=None):
    return {
        "eid": eid,
        "type": etype,
        "category": "rumor",
        "day": 1,
        "watch": 0,
        "title": "x",
        "payload": dict(payload or {}),
        "refs": {},
        "visibility": "journal",
        "schema": 1,
    }


def test_load_recomputes_next_eid_when_missing():
    payload = game_to_dict(_new_game(seed=2500))
    payload["events"]["event_history"] = [_mk_event(0), _mk_event(1), _mk_event(2)]
    payload["events"].pop("next_eid", None)
    g = _new_game(seed=2501)
    apply_game_dict(g, payload)
    eids = [int(e.get("eid", -1)) for e in (g.event_history or [])]
    assert eids[:3] == [0, 1, 2]
    assert int(getattr(g, "next_event_eid", -1)) == (max(eids) + 1 if eids else 0)


def test_load_recomputes_next_eid_when_stale():
    payload = game_to_dict(_new_game(seed=2510))
    payload["events"]["event_history"] = [_mk_event(5), _mk_event(6), _mk_event(7)]
    payload["events"]["next_eid"] = 2
    g = _new_game(seed=2511)
    apply_game_dict(g, payload)
    eids = [int(e.get("eid", -1)) for e in (g.event_history or [])]
    assert eids[:3] == [5, 6, 7]
    assert int(getattr(g, "next_event_eid", -1)) == (max(eids) + 1 if eids else 0)


def test_load_normalizes_non_int_next_eid():
    payload = game_to_dict(_new_game(seed=2520))
    payload["events"]["event_history"] = [_mk_event(2), _mk_event(4)]
    payload["events"]["next_eid"] = "abc"
    g = _new_game(seed=2521)
    apply_game_dict(g, payload)
    eids = [int(e.get("eid", -1)) for e in (g.event_history or [])]
    assert eids[:2] == [2, 4]
    assert int(getattr(g, "next_event_eid", -1)) == (max(eids) + 1 if eids else 0)


def test_load_reassigns_eids_when_history_has_invalid_or_duplicate_ids():
    payload = game_to_dict(_new_game(seed=2530))
    payload["events"]["event_history"] = [
        _mk_event(3, payload={"hint": "a"}),
        _mk_event("bad", payload={"hint": "b"}),
        _mk_event(3, payload={"hint": "c"}),
        {k: v for k, v in _mk_event(9, payload={"hint": "d"}).items() if k != "eid"},
    ]
    payload["events"]["next_eid"] = -5
    g = _new_game(seed=2531)
    apply_game_dict(g, payload)
    eids = [int(e.get("eid", -1)) for e in (g.event_history or [])]
    assert eids == list(range(len(eids)))
    assert int(getattr(g, "next_event_eid", -1)) == len(eids)


def test_append_player_event_bumps_counter_if_needed():
    g = _new_game(seed=2540)
    g.event_history = [_mk_event(10, etype="discovery.recorded", payload={"name": "X", "q": 0, "r": 0, "kind": "poi"})]
    g.next_event_eid = 1
    g._append_player_event(
        "rumor.learned",
        category="rumor",
        title="r",
        payload={"hint": "h"},
        refs={},
    )
    assert int(g.event_history[-1].get("eid", -1)) == 11
    assert int(g.next_event_eid) == 12


def test_projection_order_stable_after_normalization():
    payload = game_to_dict(_new_game(seed=2550))
    payload["events"]["event_history"] = [
        _mk_event(2, payload={"hint": "first", "day": 1}),
        _mk_event(2, payload={"hint": "second", "day": 1}),
        _mk_event(1, payload={"hint": "third", "day": 1}),
    ]
    payload["events"]["next_eid"] = 1
    g = _new_game(seed=2551)
    apply_game_dict(g, payload)
    rumors = g._journal_rumors()
    hints = [str(r.get("hint") or "") for r in rumors]
    assert hints[:3] == ["first", "second", "third"]

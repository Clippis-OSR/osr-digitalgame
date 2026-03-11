from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.wilderness_context import resolve_travel_context, first_time_poi_resolution_key


def _new_game(seed: int = 23000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_resolve_travel_context_normalizes_current_wilderness_state():
    g = _new_game()
    g.wilderness_travel_mode = "cautious"  # legacy alias should normalize.
    ctx = resolve_travel_context(g)
    assert ctx["pace"] == "careful"
    assert isinstance(ctx["encounter_mod"], int)
    assert isinstance(ctx["discovery_mod"], int)
    assert "party_ready" in ctx and "low_supplies" in ctx


def test_first_time_poi_resolution_key_is_stable_across_paths():
    poi = {"id": "poi:secondary:shrine:0,-1", "type": "shrine", "name": "Old Wayside Shrine"}
    a = first_time_poi_resolution_key(dict(poi), mode="explore")
    b = first_time_poi_resolution_key({"type": "shrine", "id": "poi:secondary:shrine:0,-1"}, mode="explore")
    c = first_time_poi_resolution_key(dict(poi), mode="interact")
    assert a == b
    assert a != c


def test_poi_first_time_reward_or_event_does_not_duplicate():
    g = _new_game(seed=23010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    assert g._cmd_travel_to_hex(0, -1).ok
    assert g._cmd_investigate_current_location().ok
    assert g._cmd_investigate_current_location().ok
    rows = [e for e in (g.event_history or []) if str(e.get("type") or "") == "poi.explored"]
    assert len(rows) == 1


def test_travel_mode_pace_impacts_params_and_context():
    g = _new_game(seed=23020)
    assert g._cmd_set_travel_mode("careful").ok
    careful = g._wilderness_travel_params()
    assert g._cmd_set_travel_mode("fast").ok
    fast = g._wilderness_travel_params()
    assert careful[1] > fast[1]
    assert careful[2] < fast[2]


def test_non_combat_wilderness_scene_resolves_cleanly():
    g = _new_game(seed=23030)
    hx = g._ensure_current_hex()

    class D6:
        def d(self, n):
            return 5

    g.wilderness_dice = D6()
    handled = g._resolve_wilderness_scene(hx, chance=4)
    assert handled is True


def test_rumor_entries_include_structured_hints():
    g = _new_game(seed=23040)
    g.gather_rumors()
    assert g.rumors
    last = g.rumors[-1]
    assert str(last.get("rumor_type") or "") in ("location", "threat", "opportunity")
    assert bool(last.get("risk_hint"))
    assert bool(last.get("reward_hint"))

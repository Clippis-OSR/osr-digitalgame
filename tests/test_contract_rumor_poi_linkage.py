from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.commands import AcceptContract

CANONICAL_ID = "poi:canonical:dungeon_entrance"
SECONDARY_RUINS_ID = "poi:secondary:ruins:1,0"


def _new_game(seed: int = 16000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _offer(cid: str, q: int, r: int, ptype: str) -> dict:
    return {
        "cid": cid,
        "faction_id": "G1",
        "kind": "scout",
        "title": "Scout lead",
        "desc": "Follow up on a reported site.",
        "target_hex": [q, r],
        "target_poi_type": ptype,
        "reward_gp": 10,
        "rep_success": 1,
        "rep_fail": -1,
        "deadline_day": 99,
        "status": "offered",
    }


def _rumor_for_poi(g: Game, poi_id: str) -> dict:
    rows = [r for r in (g.rumors or []) if str(r.get("poi_id") or "") == poi_id]
    assert rows
    return rows[0]


def test_linked_objective_and_rumor_survive_save_load():
    g = _new_game()
    g.contract_offers = [_offer("L1", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("L1")).ok

    r = _rumor_for_poi(g, CANONICAL_ID)
    assert str(r.get("linked_contract_cid") or "") == "L1"

    data = game_to_dict(g)
    g2 = _new_game(seed=16001)
    apply_game_dict(g2, data)

    r2 = _rumor_for_poi(g2, CANONICAL_ID)
    assert str(r2.get("linked_contract_cid") or "") == "L1"


def test_canonical_contracts_link_deterministically():
    g = _new_game(seed=14020)
    g.contract_offers = [
        _offer("L2A", 0, 1, "dungeon_entrance"),
        _offer("L2B", 0, 1, "dungeon_entrance"),
    ]
    assert g.dispatch(AcceptContract("L2A")).ok
    assert g.dispatch(AcceptContract("L2B")).ok

    active = {str(c.get("cid")): str(c.get("target_poi_id") or "") for c in (g.active_contracts or [])}
    assert active.get("L2A") == CANONICAL_ID
    assert active.get("L2B") == CANONICAL_ID


def test_discovery_updates_linked_rumor_state():
    g = _new_game(seed=14010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("L3", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("L3")).ok

    r0 = _rumor_for_poi(g, CANONICAL_ID)
    assert bool(r0.get("seen", False)) is False

    assert g._cmd_travel_to_hex(0, 1).ok
    r1 = _rumor_for_poi(g, CANONICAL_ID)
    assert bool(r1.get("seen", False)) is True
    assert str(r1.get("linked_contract_cid") or "") == "L3"


def test_repeated_transitions_do_not_duplicate_or_corrupt_linkage():
    g = _new_game(seed=16030)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("L4", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("L4")).ok

    for _ in range(4):
        assert g._cmd_enter_dungeon().ok
        assert g._cmd_dungeon_leave().ok
        assert g._cmd_step_toward_town().ok

    rows = [r for r in (g.rumors or []) if str(r.get("poi_id") or "") == CANONICAL_ID]
    assert len(rows) == 1
    assert str(rows[0].get("linked_contract_cid") or "") == "L4"
    active = [c for c in (g.active_contracts or []) if str(c.get("cid") or "") == "L4"]
    assert len(active) == 1
    assert str(active[0].get("target_poi_id") or "") == CANONICAL_ID


def test_presentation_rows_show_destination_seen_and_contract_linkage():
    g = _new_game(seed=14010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("L5", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("L5")).ok

    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "L5")
    label0 = g._contract_destination_label(c)
    assert "Dungeon Entrance" in label0
    assert "(0,1)" in label0
    assert "[undiscovered]" in label0

    rr0 = g._contract_rumor_link(c)
    assert isinstance(rr0, dict)
    row0 = g._render_rumor_row(rr0)
    assert "Destination:" in row0
    assert "[unseen]" in row0
    assert "Linked contract: L5" in row0

    assert g._cmd_travel_to_hex(0, 1).ok
    rr1 = g._contract_rumor_link(c)
    row1 = g._render_rumor_row(rr1)
    assert "[seen]" in row1


def test_presentation_linkage_is_stable_across_repeated_save_load_cycles():
    g = _new_game(seed=16050)
    g.contract_offers = [_offer("L6", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("L6")).ok

    for _ in range(3):
        data = game_to_dict(g)
        g2 = _new_game(seed=16051)
        apply_game_dict(g2, data)
        g = g2

    rows = [r for r in (g.rumors or []) if str(r.get("poi_id") or "") == CANONICAL_ID]
    assert len(rows) == 1

    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "L6")
    label = g._contract_destination_label(c)
    assert "Dungeon Entrance" in label
    assert "(0,1)" in label

    linked = g._contract_rumor_link(c)
    assert isinstance(linked, dict)
    rendered = g._render_rumor_row(linked)
    assert rendered.count("Linked contract: L6") == 1

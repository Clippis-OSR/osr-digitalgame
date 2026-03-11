from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.commands import AcceptContract


def _new_game(seed: int = 17000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _offer(cid: str, q: int, r: int, ptype: str) -> dict:
    return {
        "cid": cid,
        "faction_id": "G1",
        "kind": "scout",
        "title": f"Scout {cid}",
        "desc": "Follow up on a reported site.",
        "target_hex": [q, r],
        "target_poi_type": ptype,
        "reward_gp": 10,
        "rep_success": 1,
        "rep_fail": -1,
        "deadline_day": 99,
        "status": "offered",
    }


def test_expedition_prep_snapshot_stable_across_save_load():
    g = _new_game()
    g.contract_offers = [_offer("P1", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("P1")).ok

    snap1 = g.expedition_prep_snapshot()
    data = game_to_dict(g)

    g2 = _new_game(seed=17001)
    apply_game_dict(g2, data)
    snap2 = g2.expedition_prep_snapshot()

    assert snap1["active_poi_objectives"] == snap2["active_poi_objectives"]
    assert snap1["recommended_rations"] == snap2["recommended_rations"]
    assert snap1["recommended_torches"] == snap2["recommended_torches"]
    assert snap2["contracts"][0]["cid"] == "P1"
    assert "Dungeon Entrance" in snap2["contracts"][0]["destination"]


def test_poi_linked_contracts_surface_coherent_prep_info():
    g = _new_game(seed=17010)
    g.contract_offers = [_offer("P2", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("P2")).ok

    snap = g.expedition_prep_snapshot()
    assert snap["active_poi_objectives"] == 1
    row = snap["contracts"][0]
    assert row["cid"] == "P2"
    assert "Dungeon Entrance" in row["destination"]
    assert "Bring extra torches" in row["prep_hint"]


def test_repeated_transitions_do_not_corrupt_prep_objective_linkage():
    g = _new_game(seed=17020)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("P3", 0, 1, "dungeon_entrance")]
    assert g.dispatch(AcceptContract("P3")).ok

    for _ in range(4):
        assert g._cmd_enter_dungeon().ok
        assert g._cmd_dungeon_leave().ok
        assert g._cmd_step_toward_town().ok

    snap = g.expedition_prep_snapshot()
    rows = [r for r in (snap.get("contracts") or []) if str(r.get("cid") or "") == "P3"]
    assert len(rows) == 1
    assert "Dungeon Entrance" in rows[0]["destination"]

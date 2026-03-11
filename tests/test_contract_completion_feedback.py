from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.commands import AcceptContract


CANONICAL_ID = "poi:canonical:dungeon_entrance"


def _new_game(seed: int = 18000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _offer(cid: str, q: int, r: int, ptype: str, kind: str = "scout") -> dict:
    return {
        "cid": cid,
        "faction_id": "G1",
        "kind": kind,
        "title": f"Job {cid}",
        "desc": "Follow up on a reported site.",
        "target_hex": [q, r],
        "target_poi_type": ptype,
        "reward_gp": 10,
        "rep_success": 1,
        "rep_fail": -1,
        "deadline_day": 99,
        "status": "offered",
    }


def _has_line(g: Game, needle: str) -> bool:
    return any(needle in line for line in g.ui.lines)


def test_discovering_linked_poi_updates_progress_feedback():
    g = _new_game()
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("C1", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C1")).ok

    assert g._cmd_travel_to_hex(0, 1).ok
    hx = g.world_hexes.get("0,1") or {}
    g._update_contract_progress_on_hex(0, 1, hx)

    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C1")
    assert bool(c.get("visited_target", False)) is True
    assert bool(c.get("completion_ready", False)) is True
    assert _has_line(g, "Objective progress: reached destination for Job C1.")
    assert _has_line(g, "Objective ready: Job C1 can be turned in at town.")


def test_return_to_town_surfaces_ready_then_explicit_turnin_with_destination():
    g = _new_game(seed=18010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("C2", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C2")).ok

    assert g._cmd_travel_to_hex(0, 1).ok
    hx = g.world_hexes.get("0,1") or {}
    g._update_contract_progress_on_hex(0, 1, hx)
    assert g._cmd_step_toward_town().ok
    g._check_contracts_on_town_arrival()

    c2 = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C2")
    assert bool(c2.get("completion_ready", False)) is True
    assert _has_line(g, "Contract report: Job C2")

    assert g._turn_in_contract("C2").ok
    assert _has_line(g, "Contract turned in: Job C2 at Dungeon Entrance")
    assert _has_line(g, "Reward received: +10 gp | Reputation: +1")
    active = [x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C2"]
    assert not active


def test_save_load_preserves_linked_completion_progress_state():
    g = _new_game(seed=18020)
    g.contract_offers = [_offer("C3", 0, 1, "dungeon_entrance", kind="secure_entrance")]
    assert g.dispatch(AcceptContract("C3")).ok

    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C3")
    c["visited_target"] = True
    c["completion_ready"] = False
    c["target_poi_id"] = CANONICAL_ID

    data = game_to_dict(g)
    g2 = _new_game(seed=18021)
    apply_game_dict(g2, data)

    c2 = next(x for x in (g2.active_contracts or []) if str(x.get("cid") or "") == "C3")
    assert bool(c2.get("visited_target", False)) is True
    assert bool(c2.get("completion_ready", False)) is False
    assert str(c2.get("target_poi_id") or "") == CANONICAL_ID


def test_repeated_town_reports_do_not_duplicate_same_day_rows():
    g = _new_game(seed=18030)
    g.contract_offers = [_offer("C4", 0, 1, "dungeon_entrance", kind="secure_entrance")]
    assert g.dispatch(AcceptContract("C4")).ok

    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C4")
    c["visited_target"] = True
    c["target_poi_id"] = CANONICAL_ID
    g.party_hex = (0, 0)

    g._check_contracts_on_town_arrival()
    g._check_contracts_on_town_arrival()

    rows = [line for line in g.ui.lines if "Contract report: Job C4" in line]
    assert len(rows) == 1


def test_turnin_message_and_reward_stable_after_save_load():
    g = _new_game(seed=18040)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("C5", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C5")).ok

    data = game_to_dict(g)
    g2 = _new_game(seed=18041)
    apply_game_dict(g2, data)
    g2._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g2._cmd_travel_to_hex(0, 1).ok
    hx = g2.world_hexes.get("0,1") or {}
    g2._update_contract_progress_on_hex(0, 1, hx)
    assert g2._cmd_step_toward_town().ok
    gold_before = int(g2.gold)
    g2._check_contracts_on_town_arrival()

    assert g2._turn_in_contract("C5").ok
    assert _has_line(g2, "Contract turned in: Job C5 at Dungeon Entrance")
    assert _has_line(g2, "Reward received: +10 gp | Reputation: +1")
    assert int(g2.gold) == gold_before + 10


def test_repeated_town_checks_do_not_autopay_again_and_explicit_turnin_pays_once():
    g = _new_game(seed=18050)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("C6", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C6")).ok

    assert g._cmd_travel_to_hex(0, 1).ok
    hx = g.world_hexes.get("0,1") or {}
    g._update_contract_progress_on_hex(0, 1, hx)
    assert g._cmd_step_toward_town().ok

    gold_before = int(g.gold)
    g._check_contracts_on_town_arrival()
    gold_after_first = int(g.gold)
    g._check_contracts_on_town_arrival()
    gold_after_second = int(g.gold)

    turned_rows_before = [line for line in g.ui.lines if "Contract turned in: Job C6" in line]
    assert len(turned_rows_before) == 0
    assert gold_after_first == gold_before
    assert gold_after_second == gold_before

    assert g._turn_in_contract("C6").ok
    gold_after_turnin = int(g.gold)
    assert gold_after_turnin == gold_before + 10
    assert not g._turn_in_contract("C6").ok

    turned_rows = [line for line in g.ui.lines if "Contract turned in: Job C6" in line]
    reward_rows = [line for line in g.ui.lines if "Reward received: +10 gp" in line]
    assert len(turned_rows) == 1
    assert len(reward_rows) == 1


def test_ready_contracts_listed_for_explicit_turnin_action():
    g = _new_game(seed=18060)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.contract_offers = [_offer("C7", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C7")).ok

    assert g._cmd_travel_to_hex(0, 1).ok
    hx = g.world_hexes.get("0,1") or {}
    g._update_contract_progress_on_hex(0, 1, hx)
    assert g._cmd_step_toward_town().ok
    g._check_contracts_on_town_arrival()

    ready = g._ready_turnin_contracts()
    ids = [str(c.get("cid") or "") for c in ready]
    assert ids == ["C7"]


def test_deadline_failure_behavior_remains_intact():
    g = _new_game(seed=18070)
    g.contract_offers = [_offer("C8", 0, 1, "dungeon_entrance", kind="scout")]
    assert g.dispatch(AcceptContract("C8")).ok
    c = next(x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C8")
    c["deadline_day"] = 1
    g.campaign_day = 2

    g._check_contracts_on_town_arrival()
    active = [x for x in (g.active_contracts or []) if str(x.get("cid") or "") == "C8"]
    assert not active
    assert _has_line(g, "Contract failed (missed deadline): Job C8")

from sww.game import Game
from sww.save_load import apply_game_dict, game_to_dict
from sww.ui_headless import HeadlessUI


def _new_game(seed: int = 9600) -> Game:
    g = Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)
    g.generate_retainer_board(force=True)
    return g


def test_hiring_retainer_charges_cost_and_tracks_roster():
    g = _new_game(9610)
    g.gold = 100
    first = g.retainer_board[0]
    fee = int((getattr(first, "status", {}) or {}).get("retainer_hire_cost", 0) or 0)

    assert g._hire_retainer_from_board(0) is True

    assert g.gold == 100 - fee
    assert any(r.name == first.name for r in g.hired_retainers)
    assert any(r.name == first.name for r in g.retainer_roster)


def test_retainer_persists_across_save_load_with_role_and_assignment_state():
    g = _new_game(9620)
    g.gold = 100
    assert g._hire_retainer_from_board(0) is True
    assert g._assign_hired_retainer(0) is True

    data = game_to_dict(g)
    g2 = _new_game(9621)
    apply_game_dict(g2, data)

    assert len(g2.hired_retainers) == 1
    assert len(g2.active_retainers) == 1
    role = str((getattr(g2.hired_retainers[0], "status", {}) or {}).get("retainer_role") or "")
    assert role in {"torchbearer", "porter", "man_at_arms"}


def test_active_retainer_participates_in_expedition_party_on_dungeon_entry():
    g = _new_game(9630)
    g.gold = 100
    assert g._hire_retainer_from_board(0) is True
    assert g._assign_hired_retainer(0) is True

    assert g._cmd_enter_dungeon().ok

    names = {m.name for m in g.party.members}
    assert any(r.name in names for r in g.active_retainers)


def test_dismiss_and_dead_cleanup_remove_retainer_from_active_runtime_state():
    g = _new_game(9640)
    g.gold = 100
    assert g._hire_retainer_from_board(0) is True
    assert g._assign_hired_retainer(0) is True
    assert len(g.active_retainers) == 1

    assert g._dismiss_active_retainer(0) is True
    assert len(g.active_retainers) == 0

    hired = g.hired_retainers[0]
    hired.hp = 0
    g._cleanup_retainer_state()
    assert all(int(r.hp) > 0 for r in g.hired_retainers)
    assert all(int(r.hp) > 0 for r in g.retainer_roster)

from sww.dungeon_context import first_time_room_resolution_key, resolve_room_interaction_context
from sww.game import Game, PC
from sww.models import Stats
from sww.scripted_ui import ScriptedUI


def _new_game(actions: list[object] | None = None, seed: int = 3333) -> Game:
    ui = ScriptedUI(actions=actions or [], auto_default=True, echo=False)
    g = Game(ui, dice_seed=seed, wilderness_seed=seed + 1)
    g.party.members = [
        PC(name="A", hp=8, hp_max=8, ac_desc=8, hd=1, save=15, is_pc=True, stats=Stats(10, 12, 10, 10, 10, 10)),
        PC(name="B", hp=6, hp_max=8, ac_desc=8, hd=1, save=15, is_pc=True, stats=Stats(9, 9, 10, 10, 10, 10)),
    ]
    return g


def test_resolve_room_interaction_context_normalizes_room_state():
    g = _new_game()
    room = {
        "id": 7,
        "depth": 3,
        "cleared": False,
        "secret_found": True,
        "trap_disarmed": False,
        "exits": {"A": 1, "B": 2},
        "_delta": {"resolution_keys": ["scene:room:7:lore"]},
    }
    ctx = resolve_room_interaction_context(g, room)
    assert ctx["room_id"] == 7
    assert ctx["depth"] == 3
    assert ctx["secrets_found"] is True
    assert ctx["party_ready"] is True
    assert ctx["exits"] == ["A", "B"]
    assert "scene:room:7:lore" in ctx["resolution_keys"]


def test_first_time_room_resolution_key_is_stable_across_paths():
    room = {"id": 42, "name": "Mural Hall"}
    a = first_time_room_resolution_key(room, "lore:mural", mode="resolve")
    b = first_time_room_resolution_key({"id": 42}, "lore:mural", mode="resolve")
    c = first_time_room_resolution_key(room, "lore:mural", mode="discover")
    assert a == b
    assert a != c


def test_room_first_time_resolution_does_not_duplicate_clue_or_reward():
    g = _new_game(actions=[1])
    room = {
        "id": 91,
        "type": "empty",
        "cleared": False,
        "interaction_scene": "lore",
        "exits": {"A": 92},
        "_delta": {},
    }
    g._run_room_interaction_scene(room)
    gold_after_first = g.gold
    items_after_first = len(g.party_items)
    clues_after_first = len(g._journal_clues())

    g._run_room_interaction_scene(room)

    assert len(g._journal_clues()) == clues_after_first
    assert len(g.party_items) == items_after_first
    assert g.gold == gold_after_first


def test_hazard_scene_uses_first_time_gate_for_trigger():
    g = _new_game(actions=[2])  # push through
    room = {
        "id": 77,
        "type": "empty",
        "cleared": False,
        "interaction_scene": "hazard",
        "hazard_damage": "1d4",
        "_delta": {},
    }
    hp_before = sum(int(pc.hp) for pc in g.party.members)
    g._run_room_interaction_scene(room)
    hp_after_first = sum(int(pc.hp) for pc in g.party.members)
    g._run_room_interaction_scene(room)
    hp_after_second = sum(int(pc.hp) for pc in g.party.members)

    assert hp_after_first <= hp_before
    assert hp_after_second == hp_after_first

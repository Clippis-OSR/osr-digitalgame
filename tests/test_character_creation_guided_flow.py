from sww.game import Game
from sww.scripted_ui import ScriptedUI


def _run_guided(actions: list[object], seed: int = 1010):
    ui = ScriptedUI(actions=actions, auto_default=False, echo=False)
    g = Game(ui, dice_seed=seed, wilderness_seed=seed + 1)
    g.create_party_guided()
    return g, ui


def test_guided_creation_explains_invalid_race_and_alignment_choices():
    actions = [
        "1",  # one PC
        0,  # 3d6
        "Tester",
        0,  # keep rolls
        0, 0, 0, 0, 0, 0,  # assign stats
        4,  # Assassin
        1,  # Elf (invalid)
        0,  # Human
        0,  # Law (invalid)
        2,  # Chaos
        0,  # class starter kit
        0,  # confirm
    ]
    g, ui = _run_guided(actions)

    pc = g.party.pcs()[0]
    assert pc.cls == "Assassin"
    assert pc.race == "Human"
    assert pc.alignment == "Chaos"

    logs = [s.value.get("msg", "") for s in ui.transcript if s.kind == "log"]
    assert any("Assassin is unavailable for Elf" in msg for msg in logs)
    assert any("Assassin requires alignment" in msg for msg in logs)


def test_guided_creation_review_allows_back_to_equipment():
    actions = [
        "1",
        0,
        "Backer",
        0,
        0, 0, 0, 0, 0, 0,
        0,  # Fighter
        0,  # Human
        0,  # Law
        1,  # manual equipment
        2,  # mace
        1,  # leather
        1,  # shield yes
        1,  # back to equipment from review
        0,  # class starter kit
        0,  # confirm
    ]
    g, _ui = _run_guided(actions)

    pc = g.party.pcs()[0]
    assert pc.cls == "Fighter"
    assert pc.weapon == "Sword"
    assert pc.armor == "Chain"
    assert pc.shield is True


def test_guided_creation_logs_step_titles_for_clarity():
    actions = [
        "1",
        0,
        "Steps",
        0,
        0, 0, 0, 0, 0, 0,
        0,
        0,
        0,
        0,
        0,
    ]
    _g, ui = _run_guided(actions)
    titles = [s.value.get("title", "") for s in ui.transcript if s.kind == "title"]
    assert any(t.startswith("Step 1/6: Roll Attributes") for t in titles)
    assert any(t.startswith("Step 3/6: Choose Class") for t in titles)
    assert any(t.startswith("Step 6/6: Review Character") for t in titles)

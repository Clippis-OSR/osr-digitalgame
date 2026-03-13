from sww.game import Game, PC, Stats
from sww.item_templates import build_item_instance
from sww.ui_headless import HeadlessUI


class _SeqUI(HeadlessUI):
    def __init__(self, seq):
        super().__init__()
        self._seq = list(seq)

    def choose(self, prompt: str, options: list[str]) -> int:
        if self._seq:
            return int(self._seq.pop(0))
        return max(0, len(options) - 1)


def _pc(name: str = "Hero", cls: str = "Fighter", level: int = 1, xp: int = 0, hp: int = 4, hp_max: int = 8) -> PC:
    return PC(
        name=name,
        race="Human",
        hp=hp,
        hp_max=hp_max,
        ac_desc=9,
        hd=1,
        save=15,
        morale=9,
        alignment="Neutrality",
        is_pc=True,
        cls=cls,
        level=level,
        xp=xp,
        stats=Stats(10, 10, 10, 10, 10, 10),
    )


def test_town_services_resupply_and_identify_sink_gold_and_improve_readiness():
    ui = _SeqUI([2, 4, 1, 1, 4])
    g = Game(ui, dice_seed=20000, wilderness_seed=20001)
    g.gold = 100
    pc = _pc()
    pc.inventory.items.append(build_item_instance("potion.potion_healing", identified=False))
    g.party.members = [pc]

    g._town_services_menu()
    g._town_services_menu()

    assert g.gold == 69
    assert g.rations == 6
    assert g.torches == 7
    assert g.arrows == 20
    assert g.bolts == 20
    assert pc.inventory.items[0].identified is True
    assert pc.inventory.items[0].name == "Potion of Healing"


def test_expedition_prep_snapshot_includes_warnings_and_ammo_targets():
    g = Game(HeadlessUI(), dice_seed=20010, wilderness_seed=20011)
    g.party.members = [_pc(hp=3, hp_max=8)]
    g.rations = 1
    g.torches = 0
    g.arrows = 0
    g.bolts = 0

    snap = g.expedition_prep_snapshot()

    assert snap["recommended_arrows"] >= 20
    assert snap["recommended_bolts"] >= 10
    warns = " | ".join(snap.get("warnings") or [])
    assert "Low rations" in warns
    assert "Low torches" in warns
    assert "Arrow stocks" in warns
    assert "Bolt stocks" in warns
    assert "injured" in warns


def test_return_to_town_and_training_show_progression_feedback():
    g = Game(HeadlessUI(), dice_seed=20020, wilderness_seed=20021)
    pc = _pc(level=1, xp=2500, hp=4, hp_max=7)
    g.party.members = [pc]
    g.party_hex = (3, 0)
    g.gold = 500

    res = g._cmd_return_to_town()
    g.train_party()

    assert res.ok is True
    assert any("Return to Town" in ln for ln in g.ui.lines)
    assert any("to next:" in ln for ln in g.ui.lines)
    assert any("Training Hero for level" in ln for ln in g.ui.lines)
    assert any("Level-up summary:" in ln for ln in g.ui.lines)


def test_prep_minor_healing_shortcut_does_not_charge_when_uninjured():
    ui = _SeqUI([1])
    g = Game(ui, dice_seed=20030, wilderness_seed=20031)
    g.gold = 50
    g.party.members = [_pc(hp=8, hp_max=8)]

    g._town_prepare_expedition()

    assert g.gold == 50
    assert any("No one needs healing right now." in ln for ln in g.ui.lines)


def test_arms_store_purchase_surfaces_ac_and_encumbrance_feedback():
    ui = _SeqUI([0, 0, 0, 2])
    g = Game(ui, dice_seed=20040, wilderness_seed=20041)
    g.gold = 100
    g.party.members = [_pc()]

    g.town_arms_store()

    logs = "\n".join(g.ui.lines)
    assert "Bought" in logs
    assert "Auto-equip: yes" in logs
    assert "Encumbrance:" in logs
    assert any("Weapon role:" in ln or "Armor role:" in ln or "AC:" in ln for ln in g.ui.lines)


def test_basic_resupply_logs_before_after_supply_counts():
    g = Game(HeadlessUI(), dice_seed=20050, wilderness_seed=20051)
    g.gold = 30
    g.rations = 1
    g.torches = 2
    g.arrows = 3
    g.bolts = 4

    ok = g._town_buy_basic_resupply()

    assert ok is True
    assert any("rations 1->7" in ln for ln in g.ui.lines)
    assert any("arrows 3->23" in ln for ln in g.ui.lines)

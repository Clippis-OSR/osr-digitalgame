from sww.game import Game
from sww.ui_headless import HeadlessUI


class _SeqUI(HeadlessUI):
    def __init__(self, seq):
        super().__init__()
        self._seq = list(seq)

    def choose(self, prompt: str, options: list[str]) -> int:
        if self._seq:
            return int(self._seq.pop(0))
        return max(0, len(options) - 1)


def test_view_contracts_back_exits_without_stale_branching():
    ui = _SeqUI([2])  # Back from contracts menu
    g = Game(ui, dice_seed=19010, wilderness_seed=19011)

    g.view_contracts()

    assert any("Faction Contracts" in line for line in ui.lines)
    assert any("Active contracts:" in line for line in ui.lines)


def test_view_contracts_routes_offers_then_back():
    ui = _SeqUI([0, 3, 2])  # View Offers -> Back from offers -> Back from contracts
    g = Game(ui, dice_seed=19020, wilderness_seed=19021)

    g.view_contracts()

    assert any("Faction Contracts" in line for line in ui.lines)

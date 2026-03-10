from __future__ import annotations

# Lightweight validation for the QoL auto-heal-before-rest feature.
# Run: python -m scripts.validate_autoheal

from types import SimpleNamespace

from sww.game import Game
from sww.scripted_ui import ScriptedUI
from sww.models import Actor


def make_pc(name: str, cls: str, hp: int, hp_max: int, *, prepared: list[str] | None = None) -> Actor:
    a = Actor(name=name, hp=hp, hp_max=hp_max, ac_desc=9, hd=1, save=15, is_pc=True)
    a.cls = cls
    a.level = 1
    a.spells_prepared = list(prepared or [])
    return a


def main() -> None:
    ui = ScriptedUI(actions=[], auto_default=True, echo=False)
    g = Game(ui, dice_seed=123, wilderness_seed=456)

    # Setup: one cleric with both CLW and CSW prepared, one injured fighter.
    cleric = make_pc("Cleric", "Cleric", hp=6, hp_max=6, prepared=["cure light wounds", "cure serious wounds"])
    fighter = make_pc("Fighter", "Fighter", hp=1, hp_max=6)
    g.party.members = [cleric, fighter]

    # Missing HP is 5 -> CLW max 7 can cover, so it should choose CLW first.
    g._auto_spend_healing_spells_before_rest(reason="test")
    assert "cure light wounds" not in cleric.spells_prepared, "Expected CLW to be consumed"
    # Fighter should now be healed but not above max.
    assert 1 < fighter.hp <= fighter.hp_max

    # Infinite-loop protection: if all targets block magical healing, no spells should be consumed.
    cleric2 = make_pc("Cleric2", "Cleric", hp=6, hp_max=6, prepared=["cure light wounds", "cure serious wounds"])
    fighter2 = make_pc("Fighter2", "Fighter", hp=1, hp_max=6)
    g.party.members = [cleric2, fighter2]

    def always_block(_target):
        return SimpleNamespace(blocks_magical_heal=True)

    g.effects_mgr.query_modifiers = always_block  # type: ignore[assignment]
    g._auto_spend_healing_spells_before_rest(reason="test_block")
    assert cleric2.spells_prepared == ["cure light wounds", "cure serious wounds"], "Should not consume spells when all healing is blocked"
    assert fighter2.hp == 1, "Should not heal when blocked"

    print("validate_autoheal: OK")


if __name__ == "__main__":
    main()

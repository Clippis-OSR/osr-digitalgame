"""P4.10.5 sanity checks for race bonuses.

Run from p44/: python scripts/validate_race_bonuses.py
"""

from __future__ import annotations

import os
import sys

# Allow running as a script from the scripts/ directory.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sww.content import Content
from sww.dice import Dice
from sww.models import Actor
from sww import races
from sww import rules


def main() -> None:
    c = Content()
    d = Dice(seed=123)

    # Trait presence
    for r in ("Human", "Elf", "Half-elf", "Dwarf", "Halfling"):
        tr = races.get_traits(r)
        assert tr.race_id == r

    # Halfling missile bonus
    h = Actor(name="Hob", race="Halfling", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    assert races.missile_to_hit_bonus(h) == 1

    # Halfling save bonus vs magic/spell tags
    b = races.save_bonus_for_tags(h, {"magic"})
    assert b == 4
    b2 = races.save_bonus_for_tags(h, {"spell"})
    assert b2 == 4

    # Elf immunity helper should match ghoul src
    e = Actor(name="Elr", race="Elf", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    assert races.immune_to_ghoul_paralysis(e, "Ghoul") is True
    assert races.immune_to_ghoul_paralysis(e, "Ghouls") is True
    assert races.immune_to_ghoul_paralysis(e, "Wight") is False

    # Saving throw call should incorporate race bonus without crashing.
    _ = rules.saving_throw(d, h, tags={"magic"})
    _ = rules.saving_throw(d, e, tags={"paralysis"})

    print("OK: race bonuses + hooks")


if __name__ == "__main__":
    main()

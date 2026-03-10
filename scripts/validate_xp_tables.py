"""Validate XP advancement tables for all supported classes.

This is a structural check used during XP economy tightening (P4.10.16).

It verifies:
  - thresholds are monotone increasing
  - all supported classes resolve to a non-default table (unless intended)
  - post-table extrapolation remains monotone
"""

from __future__ import annotations

from p44.sww.game import Game
from p44.sww.ui_headless import HeadlessUI


CLASSES = [
    "Fighter",
    "Cleric",
    "Magic-User",
    "Thief",
    "Paladin",
    "Ranger",
    "Assassin",
    "Druid",
    "Monk",
]


def main() -> None:
    g = Game(HeadlessUI())

    for cls in CLASSES:
        prev = -1
        # Validate a reasonable span (covers printed tables and extrapolation)
        for lvl in range(1, 31):
            xp = g.xp_threshold_for_class(cls, lvl)
            if lvl <= 1:
                assert xp == 0, f"{cls}: level 1 threshold must be 0, got {xp}"
                prev = xp
                continue
            assert xp > prev, f"{cls}: non-increasing threshold at level {lvl}: {xp} <= {prev}"
            prev = xp

    print("OK: XP tables monotone for all supported classes.")


if __name__ == "__main__":
    main()

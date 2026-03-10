"""Generate a quick XP economy report.

This is intentionally lightweight and deterministic. It prints:
  - XP thresholds per class for levels 1..N
  - XP deltas between levels

Use it when tuning dungeon stocking / treasure / gold-for-xp rate.
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


def main(levels: int = 12) -> None:
    g = Game(HeadlessUI())

    for cls in CLASSES:
        print("=" * 70)
        print(cls)
        prev = 0
        for lvl in range(1, levels + 1):
            xp = g.xp_threshold_for_class(cls, lvl)
            if lvl == 1:
                print(f"  L{lvl:>2}: {xp:>10} (start)")
            else:
                d = xp - prev
                print(f"  L{lvl:>2}: {xp:>10} (+{d})")
            prev = xp
    print("=" * 70)


if __name__ == "__main__":
    main()

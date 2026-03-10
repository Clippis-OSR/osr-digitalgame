"""Quick stress harness (P3.6).

This is not a benchmark; it's a "shake the tree" script meant to catch
crashes, invariant violations, and common edge cases.

Usage:
  python -m scripts.stress_test
  STRICT_CONTENT=true python -m scripts.stress_test
"""

from __future__ import annotations

import os

from sww.game import Game
from sww.scripted_ui import ScriptedUI
from sww.commands import (
    EnterDungeon,
    DungeonAdvanceTurn,
    DungeonLeave,
    CampToMorning,
    StepTowardTown,
)


def main() -> int:
    ui = ScriptedUI(auto_default=True, echo=False)
    g = Game(ui, dice_seed=12345, wilderness_seed=67890)

    # Basic town loop / time.
    g.dispatch(CampToMorning())

    # Enter dungeon and advance turns.
    g.dispatch(EnterDungeon())
    for _ in range(150):
        g.dispatch(DungeonAdvanceTurn())

    # Leave back to town.
    g.dispatch(DungeonLeave())

    # Wander a bit (pathing / hex state churn)
    for _ in range(30):
        g.dispatch(StepTowardTown())

    strict = str(os.environ.get("STRICT_CONTENT", "")).strip().lower() in ("1", "true", "yes", "on")
    if strict and getattr(g, "content_hash_mismatch", None):
        print("WARNING: content_hash_mismatch flagged in strict run:", g.content_hash_mismatch)
        return 1

    print("STRESS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

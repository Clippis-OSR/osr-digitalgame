"""P4.10.3: Validate class progression tables.

This is a lightweight sanity check (CI-friendly) to ensure:
  - save curves resolve for all supported classes 1..21
  - spell slot tables resolve (where applicable)
  - thief/assassin/monk skill helpers don't error

Run:
  python scripts/validate_class_progressions.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from sww.classes import save_target_for_class, spell_slots_for_class, thief_skills_for_level

    classes = ["Fighter", "Cleric", "Magic-User", "Thief", "Assassin", "Druid", "Monk", "Paladin", "Ranger"]
    ok = True

    for cls in classes:
        for lvl in range(1, 22):
            try:
                sv = save_target_for_class(cls, lvl)
                if not (2 <= int(sv) <= 20):
                    raise ValueError(f"save out of range: {sv}")
                _ = spell_slots_for_class(cls, lvl)
            except Exception as e:
                ok = False
                print(f"FAIL {cls} L{lvl}: {e}")

    # Thief skill table baseline
    try:
        for lvl in range(1, 17):
            sk = thief_skills_for_level(lvl)
            assert "Climb Walls" in sk
    except Exception as e:
        ok = False
        print(f"FAIL thief skills: {e}")

    if not ok:
        return 2
    print("OK: class progression sanity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

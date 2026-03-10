"""Validate level-up pipeline basics.

Structural checks:
- Level-up HP rolls should use the dedicated 'levelup' RNG channel.
- Level-up should emit a 'level_up' event in the event log.
"""

from __future__ import annotations


def main() -> int:
    try:
        from sww.game import Game, PC
        from sww.scripted_ui import ScriptedUI
        from sww.models import Stats
    except Exception as e:
        print(f"IMPORT_FAIL: {e}")
        return 2

    ui = ScriptedUI(actions=[], auto_default=True, echo=False)
    g = Game(ui)

    pc = PC(
        name="Tester",
        cls="Fighter",
        level=1,
        xp=999999,
        hp=8,
        hp_max=8,
        ac_desc=7,
        hd=1,
        save=15,
        stats=Stats(STR=10, INT=10, WIS=10, DEX=10, CON=10, CHA=10),
        is_pc=True,
    )
    g.party.members = [pc]
    g.gold = 999999

    g.train_party()

    rng_events = list(getattr(getattr(g, "replay", None), "rng_events", []) or [])
    lvl_rng = [e for e in rng_events if str(e.get("channel") or "") == "levelup"]
    if not lvl_rng:
        print("FAIL: no levelup RNG events recorded")
        return 1

    try:
        evs = list(getattr(getattr(g, "events", None), "events", []) or [])
        if not any(getattr(ev, "name", "") == "level_up" for ev in evs):
            print("FAIL: no level_up event recorded")
            return 1
    except Exception:
        print("FAIL: could not inspect game event log")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

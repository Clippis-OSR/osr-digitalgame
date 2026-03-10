"""Create a golden replay fixture.

This is a developer tool:
  - builds a deterministic base game state
  - runs one or more Commands
  - writes base_save.json + replay.json

Then use scripts/bless_fixture.py to generate expected.json.

Usage:
  python -m scripts.make_fixture <fixture_dir> --dice-seed 123 --wild-seed 456 \
      --cmd '{"type":"TestCombat","monster_id":"orc","count":6}'

You can pass multiple --cmd.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _mk_party(game, *, variant: str = "core") -> None:
    """Make a deterministic party suitable for fixtures."""
    from sww.game import PC
    from sww.models import Stats

    if variant == "missile":
        pcs = [
            PC(name="Archer A", cls="Fighter", level=1, hp=18, hp_max=18, ac_desc=5, hd=1, save=14, is_pc=True,
               stats=Stats(14, 15, 12, 10, 10, 10), weapon="bow"),
            PC(name="Archer B", cls="Thief", level=1, hp=14, hp_max=14, ac_desc=6, hd=1, save=14, is_pc=True,
               stats=Stats(12, 16, 10, 10, 10, 10), weapon="bow"),
        ]
    elif variant == "core_magic":
        pcs = [
            PC(name="Fighter", cls="Fighter", level=2, hp=22, hp_max=22, ac_desc=4, hd=2, save=13, is_pc=True,
               stats=Stats(16, 12, 14, 10, 10, 10), weapon="sword +1"),
            PC(name="Cleric", cls="Cleric", level=2, hp=18, hp_max=18, ac_desc=5, hd=2, save=13, is_pc=True,
               stats=Stats(12, 10, 12, 10, 15, 10), weapon="mace", spells_prepared=[
                   "cure_light_wounds_lcleric_1st_level_druid_2nd_level",
                   "bless_lcleric_2nd_level",
               ]),
            PC(name="Magic-User", cls="Magic-User", level=2, hp=12, hp_max=12, ac_desc=7, hd=2, save=14, is_pc=True,
               stats=Stats(10, 12, 10, 16, 10, 10), weapon="dagger", spells_prepared=[
                   "sleep_lmagic_user_1st_level",
                   "magic_missile_lmagic_user_1st_level",
               ]),
            PC(name="Thief", cls="Thief", level=2, hp=16, hp_max=16, ac_desc=6, hd=2, save=14, is_pc=True,
               stats=Stats(12, 15, 12, 10, 10, 12), weapon="shortsword"),
        ]
    else:
        pcs = [
            PC(name="Fighter", cls="Fighter", level=2, hp=22, hp_max=22, ac_desc=4, hd=2, save=13, is_pc=True,
               stats=Stats(16, 12, 14, 10, 10, 10), weapon="sword"),
            PC(name="Cleric", cls="Cleric", level=2, hp=18, hp_max=18, ac_desc=5, hd=2, save=13, is_pc=True,
               stats=Stats(12, 10, 12, 10, 15, 10), weapon="mace", spells_prepared=[
                   "cure_light_wounds_lcleric_1st_level_druid_2nd_level",
                   "bless_lcleric_2nd_level",
               ]),
            PC(name="Magic-User", cls="Magic-User", level=2, hp=12, hp_max=12, ac_desc=7, hd=2, save=14, is_pc=True,
               stats=Stats(10, 12, 10, 16, 10, 10), weapon="dagger", spells_prepared=[
                   "sleep_lmagic_user_1st_level",
                   "magic_missile_lmagic_user_1st_level",
               ]),
            PC(name="Thief", cls="Thief", level=2, hp=16, hp_max=16, ac_desc=6, hd=2, save=14, is_pc=True,
               stats=Stats(12, 15, 12, 10, 10, 12), weapon="shortsword"),
        ]

    game.party.members = pcs
    game.rations = 24
    game.torches = 3
    game.light_on = True


def _parse_cmd(s: str) -> dict[str, Any]:
    d = json.loads(s)
    if not isinstance(d, dict) or "type" not in d:
        raise ValueError("--cmd must be a JSON dict with a 'type' field")
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture_dir", type=str)
    ap.add_argument("--dice-seed", type=int, default=11111)
    ap.add_argument("--wild-seed", type=int, default=22222)
    ap.add_argument("--party", type=str, default="core", choices=["core", "missile", "core_magic"])
    ap.add_argument("--ui-actions", type=str, default="")
    ap.add_argument("--auto-combat", type=str, default="true")
    ap.add_argument("--cmd", action="append", default=[], help="JSON dict for a command; may repeat")

    args = ap.parse_args()
    fx = Path(args.fixture_dir)
    fx.mkdir(parents=True, exist_ok=True)

    from sww.game import Game
    from sww.replay import ReplayLog
    from sww.save_load import game_to_dict
    from sww.scripted_ui import ScriptedUI

    ui_actions = json.loads(args.ui_actions) if args.ui_actions.strip() else []
    ui = ScriptedUI(actions=ui_actions, auto_default=True, echo=False)
    ui.auto_combat = str(args.auto_combat).strip().lower() in ("1", "true", "yes", "on")

    g = Game(ui, dice_seed=int(args.dice_seed), wilderness_seed=int(args.wild_seed))
    g.enable_validation = True

    # Fixtures should be stable and focused.

    _mk_party(g, variant=args.party)

    base = game_to_dict(g)

    # Start a fresh replay log from base.
    g.replay = ReplayLog(dice_seed=g.dice_seed, wilderness_seed=g.wilderness_seed)

    from sww.replay_runner import deserialize_command

    cmds = [_parse_cmd(c) for c in (args.cmd or [])]
    for cdict in cmds:
        g.dispatch(deserialize_command(cdict))

    (fx / "base_save.json").write_text(json.dumps(base, indent=2, sort_keys=True), encoding="utf-8")

    rep = g.replay.to_dict()
    # Add meta knobs if needed.
    rep.setdefault("meta", {})
    if ui_actions:
        rep["meta"]["ui_actions"] = ui_actions
    rep["meta"]["auto_combat"] = bool(ui.auto_combat)

    (fx / "replay.json").write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

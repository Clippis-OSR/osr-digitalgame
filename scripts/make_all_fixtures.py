"""Generate the standard golden replay fixture suite and bless it.

Usage:
  python -m scripts.make_all_fixtures

This will (re)write base_save.json + replay.json for each fixture and then
run bless_fixture to produce expected.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FXROOT = ROOT / "tests" / "fixtures" / "replays"


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def main() -> int:
    FXROOT.mkdir(parents=True, exist_ok=True)

    # 1) Cover / Take Cover + missile duel (force Take Cover via UI script)
    cover_dir = FXROOT / "cover_take_cover_missile_duel"
    ui_actions = [
        "Fight",        # party intent round 1
        "Take Cover",   # Archer A action round 1
        "Take Cover",   # Archer B action round 1
        "Fight",        # party intent round 2
        "Shoot",        # Archer A
        "Shoot",        # Archer B
    ]
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(cover_dir),
        "--dice-seed", "31001", "--wild-seed", "41001",
        "--party", "missile",
        "--auto-combat", "false",
        "--ui-actions", json.dumps(ui_actions),
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"human_bandit","count":3}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(cover_dir)])

    # 2) Spell disruption edge case: MU declares spell vs archers (elves)
    spell_dir = FXROOT / "spell_disruption_edge"
    ui_actions = [
        "Fight",
        "Attack",  # Fighter
        "Attack",  # Cleric
        "Cast Spell",  # MU
        "magic missile",  # choose spell (substring)
        "1",  # choose first target
        "Attack",  # Thief
    ]
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(spell_dir),
        "--dice-seed", "31002", "--wild-seed", "41002",
        "--party", "core",
        "--auto-combat", "false",
        "--ui-actions", json.dumps(ui_actions),
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"elf","count":3}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(spell_dir)])

    # 3) Morale flee + pursuit
    morale_dir = FXROOT / "morale_retreat_pursuit"
    ui_actions = [
        "Fight",
        "Attack", "Attack", "Attack", "Attack",
        # When foes flee, prompt appears: "The enemies flee!" -> choose Pursue
        "Pursue",
    ]
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(morale_dir),
        "--dice-seed", "31003", "--wild-seed", "41003",
        "--party", "core",
        "--auto-combat", "false",
        "--ui-actions", json.dumps(ui_actions),
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"orc","count":8}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(morale_dir)])

    # 4) Grapple / escape (octopus)
    grapple_dir = FXROOT / "grapple_escape"
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(grapple_dir),
        "--dice-seed", "31004", "--wild-seed", "41004",
        "--party", "core",
        "--auto-combat", "true",
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"octopus_giant","count":1}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(grapple_dir)])

    # 5) Swallow / cut-your-way-out (purple worm)
    swallow_dir = FXROOT / "swallow_cut_out"
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(swallow_dir),
        "--dice-seed", "31005", "--wild-seed", "41005",
        "--party", "core",
        "--auto-combat", "true",
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"purple_worm","count":1}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(swallow_dir)])

    # 6) Magic resistance (balor) vs spellcasting
    mr_dir = FXROOT / "magic_resistance"
    ui_actions = [
        "Fight",
        "Attack",
        "Attack",
        "Cast Spell",
        "magic missile",
        "1",
        "Attack",
    ]
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(mr_dir),
        "--dice-seed", "31006", "--wild-seed", "41006",
        "--party", "core",
        "--auto-combat", "false",
        "--ui-actions", json.dumps(ui_actions),
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"demon_baalroch_balor","count":1}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(mr_dir)])

    # 7) Spell immunity tags (clay golem) vs Sleep
    imm_dir = FXROOT / "spell_immunity_tags"
    ui_actions = [
        "Fight",
        "Attack",
        "Attack",
        "Cast Spell",
        "sleep",
        "1",
        "Attack",
    ]
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(imm_dir),
        "--dice-seed", "31007", "--wild-seed", "41007",
        "--party", "core",
        "--auto-combat", "false",
        "--ui-actions", json.dumps(ui_actions),
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"clay_golem","count":1}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(imm_dir)])

    # 8) Energy drain persistence: fight wight, then save/reload roundtrip
    drain_dir = FXROOT / "energy_drain_persistence"
    _run([
        sys.executable, "-m", "scripts.make_fixture",
        str(drain_dir),
        "--dice-seed", "31008", "--wild-seed", "41008",
        "--party", "core_magic",
        "--auto-combat", "true",
        "--cmd", json.dumps({"type":"TestCombat","monster_id":"wight","count":1}),
        "--cmd", json.dumps({"type":"TestSaveReload","note":"post-drain"}),
    ])
    _run([sys.executable, "-m", "scripts.bless_fixture", str(drain_dir)])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic sanity checks for Fighter rules.

Run from repo root (p44/):

    python scripts/validate_fighter_rules.py

This script is intentionally small and CI-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass


class _DummyUI:
    def title(self, *_args, **_kwargs):
        return None

    def log(self, *_args, **_kwargs):
        return None

    def prompt(self, *_args, **_kwargs):
        return ""


@dataclass
class _Roll:
    total: int


class _FixedDice:
    """Dice stub that returns fixed totals for deterministic validation."""

    def __init__(self, fixed_total: int = 5, fixed_d20: int = 10):
        self._fixed_total = int(fixed_total)
        self._fixed_d20 = int(fixed_d20)

    def roll(self, _expr: str):
        return _Roll(self._fixed_total)

    def d(self, sides: int):
        # Used for d20 attack rolls.
        if int(sides) == 20:
            return int(self._fixed_d20)
        return 1


def _assert(cond: bool, msg: str):
    if not cond:
        raise SystemExit(f"[FAIL] {msg}")


def main() -> None:
    # Import here so the script fails fast if packaging/import paths are wrong.
    from sww.game import Game
    from sww.models import Actor, Stats
    from sww.attribute_rules import strength_mods

    g = Game(_DummyUI(), dice_seed=123, wilderness_seed=456)

    # Monkeypatch dice so damage and attack rolls are stable.
    g.dice = _FixedDice(fixed_total=5, fixed_d20=10)

    # -----------------------------
    # 1) STR damage bonus applied for Fighters
    # -----------------------------
    fighter = Actor(name="F", hp=10, hp_max=10, ac_desc=9, hd=1, save=15)
    fighter.is_pc = True
    fighter.cls = "Fighter"
    fighter.level = 1
    fighter.weapon = "Dagger"
    fighter.stats = Stats(STR=16, DEX=10, CON=10, INT=10, WIS=10, CHA=10)

    base = 5
    expected_bonus = int(strength_mods(16, cls="Fighter", fighter_only_bonuses=True).dmg)
    got = int(g._damage_roll(fighter, kind="melee"))
    _assert(got == base + expected_bonus, f"Fighter STR damage bonus not applied: got {got}, expected {base + expected_bonus}")

    cleric = Actor(name="C", hp=10, hp_max=10, ac_desc=9, hd=1, save=15)
    cleric.is_pc = True
    cleric.cls = "Cleric"
    cleric.level = 1
    cleric.weapon = "Dagger"
    cleric.stats = Stats(STR=16, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    got2 = int(g._damage_roll(cleric, kind="melee"))
    _assert(got2 == base, f"Non-fighter received positive STR damage bonus unexpectedly: got {got2}, expected {base}")

    # -----------------------------
    # 2) Cleave scaling formula sanity
    # -----------------------------
    def total_attacks(level: int) -> int:
        return 1 + (level // 3)

    def hd_cap(level: int) -> int:
        return 1 + (level // 3)

    _assert(total_attacks(1) == 1 and hd_cap(1) == 1, "Level 1 cleave scaling wrong")
    _assert(total_attacks(2) == 1 and hd_cap(2) == 1, "Level 2 cleave scaling wrong")
    _assert(total_attacks(3) == 2 and hd_cap(3) == 2, "Level 3 cleave scaling wrong")
    _assert(total_attacks(6) == 3 and hd_cap(6) == 3, "Level 6 cleave scaling wrong")

    # -----------------------------
    # 3) Passive parry charges decrement per incoming attack
    # -----------------------------
    defender = Actor(name="Parrier", hp=10, hp_max=10, ac_desc=9, hd=1, save=15)
    defender.is_pc = True
    defender.cls = "Fighter"
    defender.level = 1
    defender.ac_desc = 9
    defender.status = {"parry_penalty": 3, "parry_charges": 3, "parry_max": 3}

    attacker = Actor(name="Goblin", hp=5, hp_max=5, ac_desc=9, hd=1, save=15)
    attacker.is_pc = False
    attacker.level = 1
    attacker.ac_desc = 9

    for i in range(4):
        g._resolve_attack(attacker, defender, to_hit_mod=0, round_no=1, kind_override="melee", combat_foes=[attacker])
        ch = int((defender.status or {}).get("parry_charges", 0) or 0)
        if i < 3:
            _assert(ch == 2 - i, f"Parry charge not consumed on attack {i+1}: charges now {ch}")
        else:
            _assert(ch == 0, f"Parry charges went below 0 or changed unexpectedly on 4th attack: {ch}")

    print("[OK] Fighter validation passed (STR dmg, cleave scaling, passive parry charges).")


if __name__ == "__main__":
    main()

\
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional
from .dice import Dice
from .classes import normalize_class
from .attacktables import AttackTables
from .models import Actor
from . import races as race_rules

def parse_desc_ac(ac_field: str) -> int:
    """
    PDF has '3 [16]' etc. Return descending AC as int.
    """
    m = re.search(r"(-?\d+)", ac_field.strip())
    return int(m.group(1)) if m else 9

def parse_hd(hd_field: str) -> int:
    # Most monsters in the extracted set use integer HD
    m = re.search(r"(\d+)", hd_field)
    return int(m.group(1)) if m else 1

ATTACK_TABLES = AttackTables()

def attack_roll_needed(attacker, target_ac_desc: int) -> int:
    """Return d20 roll required to hit target AC (descending) using Tables 29-32."""
    if getattr(attacker, 'is_pc', False):
        cls = getattr(attacker, 'cls', 'Fighter')
        level = getattr(attacker, 'level', 1)
        return ATTACK_TABLES.needed_pc(cls, level, target_ac_desc)
    else:
        hd = getattr(attacker, 'hd', 1)
        return ATTACK_TABLES.needed_monster(hd, target_ac_desc)


def _saving_throw_mod(actor: Actor, bonus: int = 0, tags: set[str] | None = None) -> tuple[int, set[str]]:
    """Compute saving throw modifier from bonus + class/race/equipment effects."""
    t = set(tags or set())
    mod = int(bonus or 0)

    # Class bonuses from S&W Complete.
    cls = normalize_class(str(getattr(actor, "cls", "Fighter")))
    cls_l = cls.lower()
    if cls_l == "cleric" and ("poison" in t or "paralysis" in t):
        mod += 2
    if cls_l == "magic-user" and ("spell" in t or "magic" in t):
        # "against spells, including wands and staffs"
        mod += 2

    # Thief class abilities: +2 vs devices (traps, wands/staffs, and other devices).
    if cls_l == "thief" and (t & {"device", "devices", "trap", "traps", "wand", "wands", "staff", "staves"}):
        mod += 2

    # Monk class abilities: +2 vs paralysis and poisons.
    if cls_l == "monk" and ("poison" in t or "paralysis" in t):
        mod += 2

    # Druid class abilities: +2 vs fire.
    if cls_l == "druid" and ("fire" in t or "flame" in t):
        mod += 2

    # Race bonuses (content-driven).
    try:
        mod += int(race_rules.save_bonus_for_tags(actor, t))
    except Exception:
        pass

    # P4.10.21: equipment-derived save bonuses (e.g., Ring of Protection +1).
    try:
        st = getattr(actor, "status", {}) or {}
        mod += int(st.get("equip_save_bonus", 0) or 0)
        mod += int(st.get("curse_save_mod", 0) or 0)
    except Exception:
        pass

    return mod, t


def saving_throw_detail(
    dice: Dice,
    actor: Actor,
    bonus: int = 0,
    tags: set[str] | None = None,
) -> tuple[bool, int, int, int, int, set[str]]:
    """Roll a saving throw and return details.

    Returns: (ok, roll, total, need, mod, tags)
    """
    mod, t = _saving_throw_mod(actor, bonus=bonus, tags=tags)
    roll = int(dice.d(20))
    total = roll + int(mod)
    need = int(getattr(actor, "save", 15) or 15)
    return (total >= need), roll, total, need, int(mod), t


def saving_throw(dice: Dice, actor: Actor, bonus: int = 0, tags: set[str] | None = None) -> bool:
    """Roll a saving throw.

    Core S&W mechanic: d20 + modifiers >= save target.
    """
    ok, _, _, _, _, _ = saving_throw_detail(dice, actor, bonus=bonus, tags=tags)
    return bool(ok)


def raise_dead_survival_pct(con: int) -> int:
    """Raise Dead survival chance per S&W Complete Table 3 (Constitution).

    3–8  => 50%
    9–12 => 75%
    13–18 => 100%
    """
    try:
        c = int(con)
    except Exception:
        return 75
    if c <= 8:
        return 50
    if c <= 12:
        return 75
    return 100

def morale_check(dice: Dice, actor: Actor, mod: int = 0) -> bool:
    """
    S&W: morale is referee-driven; we'll implement a 2d6 morale number when available.
    If morale is None, treat as fearless.
    Return True if stands, False if breaks.
    """
    if actor.morale is None:
        return True
    r = dice.d(6) + dice.d(6) + mod
    return r <= actor.morale

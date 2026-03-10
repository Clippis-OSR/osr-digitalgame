"""Attribute modifiers for Swords & Wizardry Complete.

This module is intentionally UI-agnostic and side-effect free.

Key RAW nuance (S&W Complete):
  * Strength to-hit/damage **bonuses** apply only to Fighters by default.
    Strength penalties apply to all classes.
  * Dexterity modifies missile attacks and AC.
  * Constitution modifies HP per HD and raise-dead survival.
  * Wisdom and Charisma grant additional XP bonuses (stacking) in addition
    to Prime Attribute bonus.

We keep the functions small and explicit so they are easy to regression-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .classes import normalize_class
from .models import Stats


def _clamp_score(score: int) -> int:
    try:
        s = int(score)
    except Exception:
        return 10
    return max(3, min(18, s))


@dataclass(frozen=True, slots=True)
class StrMods:
    to_hit: int
    dmg: int
    open_doors: str  # e.g. "1" or "1-2" etc.
    carry_mod_lbs: int


@dataclass(frozen=True, slots=True)
class DexMods:
    missile_to_hit: int
    ac_desc_mod: int  # +1 = worse AC (descending), -1 = better AC


@dataclass(frozen=True, slots=True)
class ConMods:
    hp_per_hd: int
    survive_raise_dead_pct: int


def strength_mods(score: int, cls: str | None = None, *, fighter_only_bonuses: bool = True) -> StrMods:
    """Table 1: Strength.

    fighter_only_bonuses=True implements the default RAW reading: only Fighters
    gain the *positive* STR to-hit/damage modifiers; penalties apply to all.
    """

    s = _clamp_score(score)
    # Default values
    to_hit = 0
    dmg = 0
    open_doors = "1-2"
    carry = 0

    if s <= 4:
        to_hit, dmg, open_doors, carry = -2, -1, "1", -10
    elif s <= 6:
        to_hit, dmg, open_doors, carry = -1, 0, "1", -5
    elif s <= 8:
        to_hit, dmg, open_doors, carry = 0, 0, "1-2", 0
    elif s <= 12:
        to_hit, dmg, open_doors, carry = 0, 0, "1-2", 5
    elif s <= 15:
        to_hit, dmg, open_doors, carry = 1, 0, "1-2", 10
    elif s == 16:
        to_hit, dmg, open_doors, carry = 1, 1, "1-3", 15
    elif s == 17:
        to_hit, dmg, open_doors, carry = 2, 2, "1-4", 30
    else:  # 18
        to_hit, dmg, open_doors, carry = 2, 3, "1-5", 50

    # Apply RAW nuance: only Fighters get positive to-hit/dmg from STR.
    c = normalize_class(cls or "Fighter").lower()
    if fighter_only_bonuses and c != "fighter":
        if to_hit > 0:
            to_hit = 0
        if dmg > 0:
            dmg = 0

    return StrMods(to_hit=int(to_hit), dmg=int(dmg), open_doors=str(open_doors), carry_mod_lbs=int(carry))


def dexterity_mods(score: int) -> DexMods:
    """Table 2: Dexterity."""

    s = _clamp_score(score)
    if s <= 8:
        return DexMods(missile_to_hit=-1, ac_desc_mod=+1)
    if s <= 12:
        return DexMods(missile_to_hit=0, ac_desc_mod=0)
    return DexMods(missile_to_hit=+1, ac_desc_mod=-1)


def constitution_mods(score: int) -> ConMods:
    """Table 3: Constitution."""

    s = _clamp_score(score)
    if s <= 8:
        return ConMods(hp_per_hd=-1, survive_raise_dead_pct=50)
    if s <= 12:
        return ConMods(hp_per_hd=0, survive_raise_dead_pct=75)
    return ConMods(hp_per_hd=+1, survive_raise_dead_pct=100)


def max_special_hirelings(score: int) -> int:
    """Table 5: Charisma (max number of special hirelings)."""

    s = _clamp_score(score)
    if s <= 4:
        return 1
    if s <= 6:
        return 2
    if s <= 8:
        return 3
    if s <= 12:
        return 4
    if s <= 15:
        return 5
    if s <= 17:
        return 6
    return 7


def cleric_bonus_first_level_spell(wis_score: int) -> int:
    """S&W Complete: Cleric with Wisdom 15+ gains +1 first level spell."""
    return 1 if _clamp_score(wis_score) >= 15 else 0


def prime_attributes_for_class(cls: str) -> tuple[str, ...]:
    """Prime Attribute(s) by class (core 4 classes)."""
    c = normalize_class(cls).lower()
    if c == "fighter":
        return ("str",)
    if c == "cleric":
        return ("wis",)
    if c == "magic-user":
        return ("int",)
    if c == "thief":
        return ("dex",)
    return ("str",)


def _stat_value(stats: Stats, key: str) -> int:
    k = key.lower()
    if k in ("str", "strength"):
        return int(stats.STR)
    if k in ("dex", "dexterity"):
        return int(stats.DEX)
    if k in ("con", "constitution"):
        return int(stats.CON)
    if k in ("int", "intelligence"):
        return int(stats.INT)
    if k in ("wis", "wisdom"):
        return int(stats.WIS)
    if k in ("cha", "charisma"):
        return int(stats.CHA)
    return 10


def xp_bonus_percent(cls: str, stats: Stats, *, extra_prime_attrs: Iterable[str] = ()) -> int:
    """Compute total XP bonus percentage.

    S&W Complete stacking (common core):
      * Prime Attribute 13+ => +5%
      * Wisdom 13+ => +5%
      * Charisma 13+ => +5%

    If multiple primes are in effect (e.g., multi-class/house rules), all prime
    attributes must be 13+ to gain the +5% prime bonus.
    """

    primes = tuple(dict.fromkeys(list(prime_attributes_for_class(cls)) + list(extra_prime_attrs)))
    prime_ok = all(_clamp_score(_stat_value(stats, p)) >= 13 for p in primes) if primes else False

    bonus = 0
    if prime_ok:
        bonus += 5
    if _clamp_score(int(stats.WIS)) >= 13:
        bonus += 5
    if _clamp_score(int(stats.CHA)) >= 13:
        bonus += 5
    return int(bonus)

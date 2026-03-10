from __future__ import annotations

"""Race traits and bonuses (P4.10.5).

We keep race behavior content-driven via data/races.json.

This module intentionally provides *small* hooks used by the engine:
 - save bonuses vs magic/spells (e.g. halflings)
 - missile to-hit bonus (e.g. halflings)
 - listen chances (non-humans 2-in-6)
 - secret door search/passive chances (elves/half-elves)
 - immunities (e.g. elf vs ghoul paralysis)
 - dwarf stonework trap notice starter rule (optional)
"""

from dataclasses import dataclass
from typing import Any

from .data import load_json


@dataclass(frozen=True, slots=True)
class RaceTraits:
    race_id: str
    darkvision_ft: int = 0
    listen_in_6: int = 1
    secret_search_in_6: int = 1
    secret_passive_in_6: int = 0
    missile_to_hit_bonus: int = 0
    melee_to_hit_bonus: int = 0
    save_bonus_magic: int = 0
    save_bonus_spell: int = 0
    immunities: tuple[str, ...] = ()
    dwarf_trap_notice_careful_in_6: int = 0
    dwarf_trap_notice_search_in_6: int = 0


_CACHE: dict[str, RaceTraits] | None = None


def normalize_race(race: str | None) -> str:
    r = str(race or "Human").strip()
    if not r:
        return "Human"
    # Normalize common variants.
    rl = r.lower()
    if rl in ("half elf", "half-elf", "halfelf"):
        return "Half-elf"
    if rl in ("halfling", "hobbit"):
        return "Halfling"
    if rl in ("dwarf", "dwarven"):
        return "Dwarf"
    if rl in ("elf", "elven"):
        return "Elf"
    if rl == "human":
        return "Human"
    # Preserve unknowns as-is (strict mode can enforce later).
    return r


def _load() -> dict[str, RaceTraits]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    raw = load_json("races.json")
    races = raw.get("races") if isinstance(raw, dict) else None
    out: dict[str, RaceTraits] = {}
    if isinstance(races, list):
        for ent in races:
            if not isinstance(ent, dict):
                continue
            rid = str(ent.get("race_id") or "").strip()
            if not rid:
                continue
            secret = ent.get("secret_door") if isinstance(ent.get("secret_door"), dict) else {}
            sb = ent.get("save_bonus") if isinstance(ent.get("save_bonus"), dict) else {}
            sb_tags = sb.get("tags") if isinstance(sb.get("tags"), dict) else {}
            th = ent.get("to_hit_bonus") if isinstance(ent.get("to_hit_bonus"), dict) else {}
            stone = ent.get("stonework") if isinstance(ent.get("stonework"), dict) else {}

            out[rid] = RaceTraits(
                race_id=rid,
                darkvision_ft=int(ent.get("darkvision_ft", 0) or 0),
                listen_in_6=int(ent.get("listen_in_6", 1) or 1),
                secret_search_in_6=int(secret.get("search_in_6", 1) or 1),
                secret_passive_in_6=int(secret.get("passive_in_6", 0) or 0),
                missile_to_hit_bonus=int(th.get("missile", 0) or 0),
                melee_to_hit_bonus=int(th.get("melee", 0) or 0),
                save_bonus_magic=int(sb_tags.get("magic", 0) or 0),
                save_bonus_spell=int(sb_tags.get("spell", 0) or 0),
                immunities=tuple(str(x) for x in (ent.get("immunities") or []) if str(x).strip()),
                dwarf_trap_notice_careful_in_6=int(stone.get("trap_notice_careful_in_6", 0) or 0),
                dwarf_trap_notice_search_in_6=int(stone.get("trap_notice_search_in_6", 0) or 0),
            )

    # Ensure at least Human exists.
    out.setdefault("Human", RaceTraits(race_id="Human"))
    _CACHE = out
    return out


def get_traits(race: str | None) -> RaceTraits:
    rid = normalize_race(race)
    lib = _load()
    return lib.get(rid) or lib["Human"]


def party_best_listen_in_6(party: Any) -> int:
    """Return best listen chance in 6 among living PCs (race-aware)."""
    best = 1
    try:
        pcs = party.pcs() if hasattr(party, "pcs") else []
    except Exception:
        pcs = []
    for pc in pcs:
        if getattr(pc, "hp", 0) <= 0:
            continue
        best = max(best, int(get_traits(getattr(pc, "race", None)).listen_in_6))
    return max(1, min(5, best))


def party_best_secret_search_in_6(party: Any) -> int:
    best = 1
    try:
        pcs = party.pcs() if hasattr(party, "pcs") else []
    except Exception:
        pcs = []
    for pc in pcs:
        if getattr(pc, "hp", 0) <= 0:
            continue
        best = max(best, int(get_traits(getattr(pc, "race", None)).secret_search_in_6))
    return max(1, min(6, best))


def party_passive_secret_notice_in_6(party: Any) -> int:
    """Passive notice chance (elves only in our default data)."""
    best = 0
    try:
        pcs = party.pcs() if hasattr(party, "pcs") else []
    except Exception:
        pcs = []
    for pc in pcs:
        if getattr(pc, "hp", 0) <= 0:
            continue
        best = max(best, int(get_traits(getattr(pc, "race", None)).secret_passive_in_6))
    return max(0, min(6, best))


def missile_to_hit_bonus(actor: Any) -> int:
    tr = get_traits(getattr(actor, "race", None))
    return int(tr.missile_to_hit_bonus)


def save_bonus_for_tags(actor: Any, tags: set[str]) -> int:
    tr = get_traits(getattr(actor, "race", None))
    b = 0
    if "magic" in tags:
        b += int(tr.save_bonus_magic)
    if "spell" in tags:
        b += int(tr.save_bonus_spell)
    return int(b)


def immune_to_ghoul_paralysis(actor: Any, src: str) -> bool:
    tr = get_traits(getattr(actor, "race", None))
    if "ghoul_paralysis" not in set(tr.immunities):
        return False
    s = str(src or "").lower()
    return "ghoul" in s


def dwarf_trap_notice_in_6(party: Any, *, mode: str = "careful") -> int:
    """Starter dwarf stonework trap noticing.

    mode:
      - "careful": moving carefully (1-in-6)
      - "search": actively searching (4-in-6)
    """
    try:
        pcs = party.pcs() if hasattr(party, "pcs") else []
    except Exception:
        pcs = []
    for pc in pcs:
        if getattr(pc, "hp", 0) <= 0:
            continue
        tr = get_traits(getattr(pc, "race", None))
        if tr.race_id == "Dwarf":
            if mode == "search":
                return max(0, min(6, int(tr.dwarf_trap_notice_search_in_6)))
            return max(0, min(6, int(tr.dwarf_trap_notice_careful_in_6)))
    return 0

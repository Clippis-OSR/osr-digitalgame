from __future__ import annotations

from dataclasses import dataclass

from .equipment import EquipmentDB


ARMOR_ORDER = ["none", "leather", "ring", "chain", "plate"]


@dataclass(frozen=True)
class ClassRestrictions:
    cls: str
    armor_max: str  # one of none/leather/ring/chain/plate
    shield_allowed: bool
    # weapons rule
    weapons_mode: str  # ANY, LIST, BLUNT_ONLY, THIEF
    weapons_allow: list[str]
    weapons_deny: list[str]


def _norm(s: str) -> str:
    return (s or "").strip()


def load_class_restrictions(raw: dict) -> dict[str, ClassRestrictions]:
    out: dict[str, ClassRestrictions] = {}
    for row in raw.get("classes", []):
        cls = _norm(row.get("class"))
        if not cls:
            continue
        out[cls.casefold()] = ClassRestrictions(
            cls=cls,
            armor_max=str(row.get("armor_max", "plate")).casefold(),
            shield_allowed=bool(row.get("shield_allowed", False)),
            weapons_mode=str(row.get("weapons_mode", "ANY")).upper(),
            weapons_allow=[_norm(x) for x in (row.get("weapons_allow") or [])],
            weapons_deny=[_norm(x) for x in (row.get("weapons_deny") or [])],
        )
    return out


def _armor_rank(cat: str) -> int:
    c = (cat or "").casefold()
    if c not in ARMOR_ORDER:
        return ARMOR_ORDER.index("plate")
    return ARMOR_ORDER.index(c)


def armor_category(armor_name: str | None) -> str:
    if not armor_name:
        return "none"
    n = armor_name.strip().casefold()
    if n in ("leather", "ring", "chain", "plate"):
        return n
    # Unknown treated as most restrictive (fails validation elsewhere)
    return n


def is_armor_allowed(restr: ClassRestrictions, armor_name: str | None) -> bool:
    want = armor_category(armor_name)
    if want == "none":
        return True
    if want not in ARMOR_ORDER:
        return False
    return _armor_rank(want) <= _armor_rank(restr.armor_max)


def is_shield_allowed(restr: ClassRestrictions) -> bool:
    return bool(restr.shield_allowed)


def _blunt_melee_names(equipdb: EquipmentDB) -> set[str]:
    blunt = set()
    for name in equipdb.melee.keys():
        low = name.casefold()
        if any(k in low for k in ["club", "flail", "hammer", "mace", "staff"]):
            blunt.add(name)
    return blunt


def legal_weapons_for_class(restr: ClassRestrictions, equipdb: EquipmentDB) -> list[str]:
    all_weapons = list(equipdb.melee.keys()) + list(equipdb.missile.keys())
    mode = restr.weapons_mode

    if mode == "ANY":
        allowed = set(all_weapons)

    elif mode == "LIST":
        allowed = set(restr.weapons_allow)

    elif mode == "BLUNT_ONLY":
        allowed = set(_blunt_melee_names(equipdb))

    elif mode == "CLERIC":
        # Cleric: blunt melee only + sling only as ranged.
        allowed = set(_blunt_melee_names(equipdb))
        for n in equipdb.missile.keys():
            if n.casefold() == "sling":
                allowed.add(n)

    elif mode == "THIEF":
        allowed = set()
        # Long/short swords + daggers + club (melee)
        for n in ["Sword, long", "Sword, short", "Dagger", "Club"]:
            if n in equipdb.melee:
                allowed.add(n)
        # Any ranged weapon except heavy crossbow
        for n in equipdb.missile.keys():
            if n.casefold() == "crossbow, heavy":
                continue
            allowed.add(n)

    else:
        allowed = set(all_weapons)

    # Assassin/Thief: no two-handed melee weapons (ranged unaffected).
    if restr.cls.casefold() in ("assassin", "thief"):
        filtered = set()
        for w in allowed:
            if w in equipdb.melee:
                low = w.casefold()
                if "two-handed" in low or "(two-handed)" in low:
                    continue
            filtered.add(w)
        allowed = filtered

    # Apply denies
    for d in restr.weapons_deny:
        if d in allowed:
            allowed.remove(d)

    # Filter to those that exist in equipdb
    allowed2 = []
    for w in sorted(allowed):
        if w in equipdb.melee or w in equipdb.missile:
            allowed2.append(w)
    return allowed2


def is_weapon_allowed(restr: ClassRestrictions, weapon_name: str | None, equipdb: EquipmentDB) -> bool:
    if not weapon_name:
        return True
    wn = weapon_name.strip()
    return wn in set(legal_weapons_for_class(restr, equipdb))

from __future__ import annotations

from typing import Any


def normalize_class(cls: str) -> str:
    c = (cls or "Fighter").strip().lower()
    if c in ("magic user", "magic-user", "mu", "mage"):
        return "Magic-User"
    if c in ("fighter", "warrior"):
        return "Fighter"
    if c in ("cleric", "priest"):
        return "Cleric"
    if c in ("thief", "rogue"):
        return "Thief"
    if c in ("assassin", "assn"):
        return "Assassin"
    if c in ("druid",):
        return "Druid"
    if c in ("monk",):
        return "Monk"
    if c in ("paladin", "pally"):
        return "Paladin"
    if c in ("ranger",):
        return "Ranger"
    # Default fallback
    return "Fighter"


def save_target_for_class(cls: str, level: int) -> int:
    """Single-save target (d20 >= save), strict S&W Complete tables.

    Swords & Wizardry Complete uses a single saving throw value in each class
    advancement table.
    """
    c = normalize_class(cls).lower()
    lvl = max(1, int(level or 1))

    tables: dict[str, list[int]] = {
        # Table 11: Fighter Advancement Table
        "fighter": [
            14, 13, 12, 11, 10, 9, 8, 7, 6, 5,
            4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
            4,
        ],
        # Table 8: Cleric Advancement Table
        "cleric": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 4, 4, 4, 4, 4, 4, 4, 4, 4,
            4,
        ],
        # Table 12: Magic-User Advancement Table
        "magic-user": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
            5,
        ],
        # Table 19: Thief Advancement Table
        "thief": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 5,
        ],

        # Table 7: Assassin Advancement Table
        "assassin": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
            5,
        ],

        # Table 9: Druid Advancement Table
        "druid": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 4, 4, 4, 4, 4, 4, 4, 4, 4,
            4,
        ],

        # Table 13: Monk Advancement Table
        "monk": [
            15, 14, 13, 12, 11, 10, 9, 8, 7, 6,
            5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
            5,
        ],

        # Table 15: Paladin Advancement Table
        "paladin": [
            12, 11, 10, 9, 8, 7, 6, 5, 4, 3,
            2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
            2,
        ],

        # Table 16: Ranger Advancement Table
        "ranger": [
            14, 13, 12, 11, 10, 9, 8, 7, 6, 5,
            4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
            4,
        ],
    }

    if c not in tables:
        c = "fighter"
    arr = tables[c]
    if lvl <= len(arr):
        return int(arr[lvl - 1])
    return int(arr[-1])


def spell_slots_for_class(cls: str, level: int) -> dict[int, int]:
    """Return spell slots by spell level, strict S&W Complete tables."""
    c = normalize_class(cls).lower()
    lvl = max(1, int(level or 1))

    # Table 12: Magic-User Advancement Table (spell levels 1..9)
    mu_rows: dict[int, list[int]] = {
        1:  [1, 0, 0, 0, 0, 0, 0, 0, 0],
        2:  [2, 0, 0, 0, 0, 0, 0, 0, 0],
        3:  [3, 1, 0, 0, 0, 0, 0, 0, 0],
        4:  [3, 2, 0, 0, 0, 0, 0, 0, 0],
        5:  [4, 2, 1, 0, 0, 0, 0, 0, 0],
        6:  [4, 2, 2, 0, 0, 0, 0, 0, 0],
        7:  [4, 3, 2, 1, 0, 0, 0, 0, 0],
        8:  [4, 3, 3, 2, 0, 0, 0, 0, 0],
        9:  [4, 3, 3, 2, 1, 0, 0, 0, 0],
        10: [4, 4, 3, 2, 2, 0, 0, 0, 0],
        11: [4, 4, 4, 3, 3, 0, 0, 0, 0],
        12: [4, 4, 4, 4, 4, 1, 0, 0, 0],
        13: [5, 5, 5, 4, 4, 2, 0, 0, 0],
        14: [5, 5, 5, 4, 4, 3, 1, 0, 0],
        15: [5, 5, 5, 5, 4, 4, 2, 0, 0],
        16: [5, 5, 5, 5, 5, 5, 2, 1, 0],
        17: [6, 6, 6, 5, 5, 5, 2, 2, 0],
        18: [6, 6, 6, 6, 6, 5, 2, 2, 1],
        19: [7, 7, 7, 6, 6, 6, 3, 2, 2],
        20: [7, 7, 7, 7, 7, 7, 3, 3, 2],
        21: [8, 8, 8, 7, 7, 7, 4, 3, 3],
    }

    # Table 8: Cleric Advancement Table (spell levels 1..7)
    clr_rows: dict[int, list[int]] = {
        1:  [0, 0, 0, 0, 0, 0, 0],
        2:  [1, 0, 0, 0, 0, 0, 0],
        3:  [2, 0, 0, 0, 0, 0, 0],
        4:  [2, 1, 0, 0, 0, 0, 0],
        5:  [2, 2, 0, 0, 0, 0, 0],
        6:  [2, 2, 1, 1, 0, 0, 0],
        7:  [2, 2, 2, 1, 1, 0, 0],
        8:  [2, 2, 2, 2, 2, 0, 0],
        9:  [3, 3, 3, 2, 2, 0, 0],
        10: [3, 3, 3, 3, 3, 0, 0],
        11: [4, 4, 4, 3, 3, 0, 0],
        12: [4, 4, 4, 4, 4, 1, 0],
        13: [5, 5, 5, 4, 4, 1, 0],
        14: [5, 5, 5, 5, 5, 2, 0],
        15: [6, 6, 6, 5, 5, 2, 0],
        16: [6, 6, 6, 6, 6, 3, 0],
        17: [7, 7, 7, 6, 6, 3, 1],
        18: [7, 7, 7, 7, 7, 4, 1],
        19: [8, 8, 8, 7, 7, 4, 2],
        20: [8, 8, 8, 8, 8, 5, 2],
        21: [9, 9, 9, 8, 8, 5, 3],
    }

    # Table 9: Druid Advancement Table (spell levels 1..7)
    dru_rows: dict[int, list[int]] = {
        1:  [1, 0, 0, 0, 0, 0, 0],
        2:  [2, 1, 0, 0, 0, 0, 0],
        3:  [3, 1, 0, 0, 0, 0, 0],
        4:  [3, 1, 1, 0, 0, 0, 0],
        5:  [3, 2, 1, 0, 0, 0, 0],
        6:  [3, 2, 2, 0, 0, 0, 0],
        7:  [4, 2, 2, 1, 0, 0, 0],
        8:  [4, 3, 2, 1, 0, 0, 0],
        9:  [4, 3, 3, 2, 0, 0, 0],
        10: [5, 3, 3, 2, 1, 0, 0],
        11: [5, 3, 3, 3, 2, 1, 0],
        12: [5, 4, 4, 4, 3, 2, 1],
        13: [6, 5, 5, 4, 4, 3, 2],
        14: [7, 5, 5, 4, 4, 3, 2],
        15: [7, 6, 5, 4, 4, 3, 2],
        16: [7, 6, 6, 4, 4, 3, 2],
        17: [8, 6, 6, 5, 4, 3, 2],
        18: [8, 7, 6, 5, 5, 3, 2],
        19: [9, 8, 6, 5, 5, 3, 2],
        20: [9, 8, 7, 5, 5, 3, 2],
        21: [9, 8, 7, 6, 5, 3, 2],
    }

    # Table 16: Ranger Advancement Table (spell levels 1..3).
    # The book distinguishes Cleric vs Magic-User spells; the core engine
    # currently stores a single slot map, so we use TOTAL slots per spell
    # level and allow preparation from the union of those lists.
    rng_rows: dict[int, list[int]] = {
        1:  [0, 0, 0],
        2:  [0, 0, 0],
        3:  [0, 0, 0],
        4:  [0, 0, 0],
        5:  [0, 0, 0],
        6:  [0, 0, 0],
        7:  [0, 0, 0],
        8:  [0, 0, 0],
        9:  [1, 0, 0],
        10: [3, 1, 0],
        11: [4, 2, 0],
        12: [5, 3, 1],
        13: [6, 4, 2],
        14: [7, 5, 2],
        15: [8, 6, 2],
        16: [8, 6, 3],
        17: [8, 6, 4],
        18: [9, 7, 4],
        19: [10, 8, 4],
        20: [10, 8, 5],
        21: [10, 8, 6],
    }

    if c == "magic-user":
        row = mu_rows.get(min(lvl, 21), mu_rows[21])
        return {i + 1: int(n) for i, n in enumerate(row) if int(n) > 0}
    if c == "cleric":
        row = clr_rows.get(min(lvl, 21), clr_rows[21])
        return {i + 1: int(n) for i, n in enumerate(row) if int(n) > 0}
    if c == "druid":
        row = dru_rows.get(min(lvl, 21), dru_rows[21])
        return {i + 1: int(n) for i, n in enumerate(row) if int(n) > 0}
    if c == "ranger":
        row = rng_rows.get(min(lvl, 21), rng_rows[21])
        return {i + 1: int(n) for i, n in enumerate(row) if int(n) > 0}
    return {}


def thief_racial_skill_bonus(race: str | None) -> dict[str, int]:
    """Table 18: Non-human Thief Bonuses."""
    r = (race or "").strip().lower()
    if r == "dwarf":
        return {"Find/Remove Traps": 10, "Hide in Shadows": 5, "Move Silently": 5, "Open Locks": 5}
    if r == "elf":
        return {"Hide in Shadows": 15, "Move Silently": 10}
    if r == "halfling":
        return {"Find/Remove Traps": 5, "Hide in Shadows": 10, "Move Silently": 10, "Open Locks": 10}
    return {}


def apply_thief_racial_bonuses(skills: dict[str, int], race: str | None) -> dict[str, int]:
    bonus = thief_racial_skill_bonus(race)
    if not bonus:
        return dict(skills)
    out = dict(skills)
    for k, v in bonus.items():
        if k in out:
            out[k] = min(100, int(out[k]) + int(v))
    return out


def thief_backstab_multiplier(level: int) -> int:
    """S&W backstab damage multiplier by level band."""
    lvl = max(1, int(level or 1))
    if lvl >= 13:
        return 5
    if lvl >= 9:
        return 4
    if lvl >= 5:
        return 3
    return 2


def starting_spells_for_class(cls: str, content: Any, rng: Any) -> list[str]:
    """Pick starting known spells for a class.

    Currently only used for Magic-Users at level 1.
    """
    c = normalize_class(cls).lower()
    if c != "magic-user":
        return []

    # Try to select from content DB using spell_level string.
    spells = []
    try:
        all_spells = content.list_spells_for_class("Magic-User")
        for s in all_spells:
            if "1st" in (s.spell_level or "").lower():
                spells.append(s.name)
    except Exception:
        spells = []

    if not spells:
        return []

    # Deterministic choice via provided rng
    try:
        choice = rng.choice(spells)
    except Exception:
        # best effort fallback
        choice = spells[0]
    return [choice]


def thief_skills_for_level(level: int) -> dict[str, int]:
    """Thief skill table.

    Uses Swords & Wizardry Complete "Table 17: Thieving Skills".

    Notes:
    - We map "Delicate Tasks and Traps" to "Find/Remove Traps".
    - Hear Noise is stored as an "in 6" value (e.g., 3 means 3-in-6).
    - For level 16+, values remain at the table's cap.
    """
    lvl = max(1, int(level or 1))
    idx = min(lvl, 16) - 1

    climb = [85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 99]
    traps = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 100, 100, 100]
    hear =  [3,  3,  4,  4,  4,  4,  5,  5,  5,  5,  6,  6,  6,  6,  6,  6]
    hide =  [10, 15, 20, 25, 30, 35, 40, 55, 65, 75, 85, 95, 100, 100, 100, 100]
    move =  [20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 100, 100, 100, 100]
    open_ = [10, 15, 20, 25, 30, 35, 40, 55, 65, 75, 85, 95, 100, 100, 100, 100]

    return {
        "Climb Walls": int(climb[idx]),
        "Find/Remove Traps": int(traps[idx]),
        "Hear Noise": int(hear[idx]),
        "Hide in Shadows": int(hide[idx]),
        "Move Silently": int(move[idx]),
        "Open Locks": int(open_[idx]),
    }

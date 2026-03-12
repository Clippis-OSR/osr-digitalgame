from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.I)


@dataclass
class LootRoll:
    table_id: str
    gp_from_coins: int
    gp_from_gems: int
    gp_from_jewelry: int
    gems_count: int
    jewelry_count: int
    magic_count: int
    # Materialized magic items (Table 78 trade-outs). Each entry is a plain dict
    # suitable for persistence in save files.
    magic_items: list[dict[str, Any]] | None = None
    # Trade-out bookkeeping (Table 78)
    tradeouts_100: int = 0
    tradeouts_1000: int = 0
    tradeouts_5000: int = 0
    major_treasure: bool = False

    @property
    def gp_total(self) -> int:
        return int(self.gp_from_coins) + int(self.gp_from_gems) + int(self.gp_from_jewelry)



def _generic_unidentified_name(kind: str) -> str:
    k = (kind or "item").lower().strip()
    if k.startswith("potion"):
        return "Mysterious Potion"
    if k.startswith("scroll"):
        return "Unmarked Scroll"
    if k == "wand":
        return "Carved Wand"
    if k == "staff":
        return "Runed Staff"
    if k == "ring":
        return "Jeweled Ring"
    if k == "weapon":
        return "Fine Weapon"
    if k == "armor" or k == "shield":
        return "Fine Armor"
    return "Mysterious Item"


def _wrap_magic_item_instance(*, item: dict[str, Any], instance_tag: str) -> dict[str, Any]:
    """Attach identification/curse fields for persistent magic items.

    We store:
      - true_name: the actual rolled item name
      - name: what the party sees (generic until identified / aura-seen)
      - identified: whether the true name is known
      - aura: Detect Magic has revealed it is magical (but not its exact nature)
      - cursed: whether the item is cursed
    """
    it = dict(item or {})
    true_name = str(it.get("name") or it.get("true_name") or "Unknown Item")
    kind = str(it.get("kind") or "").lower().strip() or "item"
    # Heuristic curse detection: cursed kinds or names containing 'cursed'.
    cursed = bool(it.get("cursed", False))
    if (not cursed) and ("cursed" in true_name.lower() or kind in {"scroll_cursed", "item_cursed"}):
        cursed = True
    # Stable instance id
    slug = re.sub(r"[^a-z0-9]+", "_", true_name.strip().lower()).strip("_") or "item"
    it["item_instance_id"] = str(it.get("item_instance_id") or f"{slug}::{instance_tag}")
    it["true_name"] = true_name
    it["identified"] = bool(it.get("identified", False))
    it["aura"] = bool(it.get("aura", False))
    it["cursed"] = bool(cursed)
    if not it["identified"]:
        it["name"] = _generic_unidentified_name(kind)
    else:
        it["name"] = true_name
    return it

def _roll_expr(rng, expr: str) -> int:
    """Roll minimal expressions like '1d6', '1d6*10', '2d4+3', and plain integers.

    Uses the provided RNG (LoggedRandom / ReplayRandom). This keeps replay strict.
    """
    s = str(expr).replace(" ", "")
    if not s:
        return 0
    if "+" in s:
        return sum(_roll_expr(rng, p) for p in s.split("+"))
    if "*" in s:
        left, right = s.split("*", 1)
        base = _roll_expr(rng, left)
        try:
            mul = int(right)
        except ValueError:
            mul = _roll_expr(rng, right)
        return int(base) * int(mul)
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)

    m = _DICE_RE.match(s)
    if not m:
        raise ValueError(f"Bad dice expression: {expr}")
    n = int(m.group(1) or "1")
    sides = int(m.group(2))
    mod = int((m.group(3) or "0").replace(" ", ""))
    total = sum(int(rng.randint(1, sides)) for _ in range(n)) + int(mod)
    return int(total)


def _pick_band_1d100(rng, bands: list[dict[str, Any]]) -> dict[str, Any] | None:
    roll = int(rng.randint(1, 100))
    for b in bands:
        if int(b.get("lo", 0)) <= roll <= int(b.get("hi", 0)):
            return b
    return None


def roll_hoard(*, game, table_id: str, budget_gp: int | None = None) -> LootRoll:
    """Roll a compiled hoard table and return gp-equivalent loot.

    Note: magic lists may be unresolved in minimal content sets; in that case,
    we count 'magic' picks but do not materialize items.
    """
    comp = getattr(getattr(game, "content", None), "treasure_compiled", None) or {}
    tables = (comp.get("tables_by_id") or {}) if isinstance(comp, dict) else {}
    vt_by_id = (comp.get("value_tables_by_id") or {}) if isinstance(comp, dict) else {}
    ml_by_id = (comp.get("magic_lists_by_id") or {}) if isinstance(comp, dict) else {}

    table = tables.get(str(table_id)) or {}
    entries = table.get("entries") or []
    if not entries:
        return LootRoll(table_id=str(table_id), gp_from_coins=0, gp_from_gems=0, gp_from_jewelry=0, gems_count=0, jewelry_count=0, magic_count=0)

    roll = int(game.dice_rng.randint(1, 100))
    entry = None
    for e in entries:
        if int(e.get("lo", 0)) <= roll <= int(e.get("hi", 0)):
            entry = e
            break
    entry = entry or entries[-1]

    gp_coins = 0
    gp_gems = 0
    gp_jewels = 0
    gems_n = 0
    jewels_n = 0
    magic_n = 0

    for drop in (entry.get("drops") or []):
        dtype = str(drop.get("type") or "").lower().strip()
        if dtype == "coins":
            rolls = drop.get("rolls") or {}
            # Convert to gp-equivalent using classic ratios.
            # cp=1/100 gp, sp=1/10 gp, ep=1/2 gp, gp=1 gp, pp=5 gp.
            cp = _roll_expr(game.dice_rng, (rolls.get("cp") or {}).get("expr", "0"))
            sp = _roll_expr(game.dice_rng, (rolls.get("sp") or {}).get("expr", "0"))
            ep = _roll_expr(game.dice_rng, (rolls.get("ep") or {}).get("expr", "0"))
            gp = _roll_expr(game.dice_rng, (rolls.get("gp") or {}).get("expr", "0"))
            pp = _roll_expr(game.dice_rng, (rolls.get("pp") or {}).get("expr", "0"))
            gp_coins += int(gp)
            gp_coins += int(sp) // 10
            gp_coins += int(ep) // 2
            gp_coins += int(cp) // 100
            gp_coins += int(pp) * 5
        elif dtype == "gems":
            cnt = int(_roll_expr(game.dice_rng, (drop.get("count") or {}).get("expr", "0")))
            gems_n += int(cnt)
            vt = vt_by_id.get(str(drop.get("value_table_id") or "")) or {}
            bands = vt.get("bands") or []
            # Roll each gem value deterministically.
            for _ in range(int(cnt)):
                band = _pick_band_1d100(game.dice_rng, bands) or {}
                gp_gems += int(band.get("gp_value", 0) or 0)
        elif dtype in ("jewelry", "jewels"):
            cnt = int(_roll_expr(game.dice_rng, (drop.get("count") or {}).get("expr", "0")))
            jewels_n += int(cnt)
            vt = vt_by_id.get(str(drop.get("value_table_id") or "")) or {}
            bands = vt.get("bands") or []
            for _ in range(int(cnt)):
                band = _pick_band_1d100(game.dice_rng, bands) or {}
                gp_jewels += int(band.get("gp_value", 0) or 0)
        elif dtype == "magic":
            picks = int(_roll_expr(game.dice_rng, (drop.get("picks") or {}).get("expr", "0")))
            magic_n += int(picks)
            # Minimal content sets may not have resolved item_ids. If they do, pick deterministically.
            ml = ml_by_id.get(str(drop.get("magic_list_id") or "")) or {}
            item_ids = list(ml.get("item_ids") or [])
            # If item ids exist, we could materialize them later; for now, count only.
            _ = item_ids

    # If a gp budget is provided (e.g., XP-based hoard sizing), treat gems/jewelry as fixed picks
    # and allocate remaining value to coins.
    if budget_gp is not None:
        try:
            budget = int(budget_gp)
        except Exception:
            budget = 0
        noncoin = int(gp_gems) + int(gp_jewels)
        gp_coins = max(0, int(budget) - int(noncoin))

    return LootRoll(
        table_id=str(table_id),
        gp_from_coins=int(gp_coins),
        gp_from_gems=int(gp_gems),
        gp_from_jewelry=int(gp_jewels),
        gems_count=int(gems_n),
        jewelry_count=int(jewels_n),
        magic_count=int(magic_n),
    )


def _tradeout_gem_value(*, game, tier: str) -> int:
    """Gem/jewelry value roll for Tables 79/80/81."""
    t = str(tier).lower().strip()
    r = int(game.dice_rng.randint(1, 4))
    if t == "minor":
        # Table 79
        if r == 1:
            return _roll_expr(game.dice_rng, "1d6")
        if r == 2:
            return _roll_expr(game.dice_rng, "1d100+25")
        if r == 3:
            return _roll_expr(game.dice_rng, "1d100+75")
        return _roll_expr(game.dice_rng, "1d100*10")
    if t == "medium":
        # Table 80
        if r == 1:
            return _roll_expr(game.dice_rng, "1d100")
        if r == 2:
            return _roll_expr(game.dice_rng, "1d6*200")
        if r == 3:
            return _roll_expr(game.dice_rng, "1d6*300")
        return _roll_expr(game.dice_rng, "1d100*100")
    # Table 81 (major)
    if r == 1:
        return _roll_expr(game.dice_rng, "1d100*10")
    if r == 2:
        return _roll_expr(game.dice_rng, "1d100*80")
    if r == 3:
        return _roll_expr(game.dice_rng, "1d100*120")
    return _roll_expr(game.dice_rng, "1d100*200")


def _tradeout_magic_count(*, game, tier: str) -> int:
    """(deprecated) kept for backward compatibility."""
    return len(_tradeout_magic_items(game=game, tier=tier))


def _t79_107(game) -> dict[str, Any]:
    try:
        return dict(getattr(getattr(game, "content", None), "treasure_tables_79_107", {}) or {})
    except Exception:
        return {}


def _table_entry_by_index(game, table_id: str, index_1_based: int) -> dict[str, Any] | None:
    """Pick the entry at a 1-based index from a treasure table."""
    t = _t79_107(game).get(str(table_id)) or {}
    entries = list(t.get("entries") or [])
    idx = int(index_1_based)
    if idx < 1 or idx > len(entries):
        return None
    return entries[idx - 1]


def _table_entry_by_die_roll(game, table_id: str, lo: int, hi: int) -> dict[str, Any] | None:
    """Pick a treasure table entry by rolling within a numeric band."""
    t = _t79_107(game).get(str(table_id)) or {}
    entries = list(t.get("entries") or [])
    if not entries:
        return None
    roll = int(game.dice_rng.randint(int(lo), int(hi)))
    for e in entries:
        if int(e.get("lo", 0)) <= roll <= int(e.get("hi", 0)):
            return e
    return entries[-1]


def _roll_potion(game) -> dict[str, Any]:
    # Table 85: Potions (1d100)
    e = _table_entry_by_die_roll(game, "85", 1, 100) or {"result": "Unknown"}
    name = str(e.get("result") or "Unknown")
    return {"kind": "potion", "name": f"Potion of {name}"}


def _roll_scroll_from_table86_index(game, idx: int) -> dict[str, Any]:
    """Table 86 is 1..18; minor uses 1..6, medium 7..12, major 13..18."""
    e = _table_entry_by_index(game, "86", idx) or {"result": "Unknown"}
    res = str(e.get("result") or "Unknown")
    res_l = res.lower()

    # Protection scrolls (Table 87)
    if "protection" in res_l:
        pe = _table_entry_by_die_roll(game, "87", 1, 12) or {"result": "Protection"}
        return {"kind": "scroll_protection", "name": f"Scroll of {pe.get('result')}"}

    # Cursed scrolls (Table 88)
    if "cursed" in res_l:
        ce = _table_entry_by_die_roll(game, "88", 1, 12) or {"result": "Cursed Scroll"}
        return {"kind": "scroll_cursed", "name": str(ce.get("result") or "Cursed Scroll")}

    # Spell scrolls
    # Parse strings like:
    #  "1 spell, level 1" / "2 spells, level 1d2 each" / "5 spells, level 1d6 each"
    #  "1 spell, level 1d6" / "2 spells, level 1d6+1 each"
    # Deterministic interpretation:
    #  - Determine spell count
    #  - For each spell, roll spell level as indicated
    #  - Choose a random spell of that level from canonical compiled lists
    import re

    m = re.search(r"^(\d+)\s*spells?\s*,\s*level\s*(.+)$", res, re.I)
    if not m:
        return {"kind": "scroll", "name": f"Scroll ({res})", "spells": []}

    n = int(m.group(1))
    tail = m.group(2).strip()
    tail = tail.replace("each", "").strip()

    spells: list[str] = []
    for _ in range(max(1, n)):
        lvl = max(1, int(_roll_expr(game.dice_rng, tail)))
        spells.append(_pick_spell_id_at_level(game, lvl))
    return {"kind": "scroll", "name": f"Scroll ({n} spell{'s' if n!=1 else ''})", "spells": spells}


def _pick_spell_id_at_level(game, level: int) -> str:
    """Pick a spell_id at the given spell level from canonical compiled spell lists.

    Falls back to any known spell name if compiled lists are absent.
    """
    lvl = int(max(1, int(level)))
    content = getattr(game, "content", None)
    comp = getattr(content, "spell_lists_compiled", None) or {}
    classes = comp.get("classes") if isinstance(comp, dict) else None
    pool: list[str] = []
    if isinstance(classes, dict):
        seen = set()
        for _, levels in classes.items():
            if not isinstance(levels, dict):
                continue
            ids = levels.get(str(lvl)) or []
            for ent in ids:
                sid = str((ent or {}).get("spell_id") or "").strip()
                if sid and sid not in seen:
                    seen.add(sid)
                    pool.append(sid)
    if not pool:
        # Best-effort fallback: use any spell name
        spells = list(getattr(content, "spells", []) or [])
        if spells:
            s = spells[int(game.dice_rng.randint(1, len(spells))) - 1]
            return str(getattr(s, "spell_id", getattr(s, "name", "unknown")) or "unknown")
        return "unknown"
    pool = sorted(pool)
    idx = int(game.dice_rng.randint(1, len(pool))) - 1
    return pool[idx]


def _roll_magic_armor_weapons_from_table89_index(game, idx: int) -> dict[str, Any]:
    e = _table_entry_by_index(game, "89", idx) or {"result": "Unknown"}
    res = str(e.get("result") or "Unknown")
    res_l = res.lower()

    # Cursed armor/shield: Table 90
    if "cursed armor" in res_l:
        ce = _table_entry_by_die_roll(game, "90", 1, 8) or {"result": "Cursed"}
        return {"kind": "armor", "name": f"Cursed armor/shield ({ce.get('result')})", "cursed": True}

    # Cursed weapon: reuse Table 90 (same curse descriptors)
    if "cursed weapon" in res_l:
        ce = _table_entry_by_die_roll(game, "90", 1, 8) or {"result": "Cursed"}
        wep = _roll_weapon_name_from_table("91", game)
        return {"kind": "weapon", "name": f"{wep} ({ce.get('result')})", "cursed": True}

    # Shields
    if "shield" in res_l:
        bonus = _extract_bonus(res)
        return {"kind": "shield", "name": f"Shield +{bonus}", "bonus": bonus}

    # Armor
    if "armor" in res_l:
        bonus = _extract_bonus(res)
        an = _roll_armor_name_from_table(game)
        return {"kind": "armor", "name": f"{an} +{bonus}", "bonus": bonus}

    # Missile weapons
    if "missile" in res_l:
        bonus = _extract_bonus(res)
        mw = _roll_weapon_name_from_table("94", game)
        return {"kind": "weapon_missile", "name": f"{mw} +{bonus}", "bonus": bonus}

    # Melee weapons
    if "melee weapon" in res_l:
        bonus = _extract_bonus(res)
        wep = _roll_weapon_name_from_table("91", game)
        item = {"kind": "weapon", "name": f"{wep} +{bonus}", "bonus": bonus}
        if "minor ability" in res_l:
            abil = _table_entry_by_die_roll(game, "95", 1, 20) or {"result": "Minor ability"}
            item["minor_ability"] = str(abil.get("result") or "Minor ability")
        return item

    if "unusual weapon" in res_l:
        ue = _table_entry_by_die_roll(game, "96", 1, 20) or {"result": "Unusual weapon"}
        return {"kind": "weapon", "name": str(ue.get("result") or "Unusual weapon")}
    if "unusual armor" in res_l:
        ua = _table_entry_by_die_roll(game, "97", 1, 20) or {"result": "Unusual armor"}
        return {"kind": "armor", "name": str(ua.get("result") or "Unusual armor")}

    return {"kind": "magic", "name": res}


def _extract_bonus(text: str) -> int:
    import re

    m = re.search(r"\+(\d)", str(text))
    return int(m.group(1)) if m else 1


def _roll_weapon_name_from_table(table_id: str, game) -> str:
    # Table 91 (melee types) or Table 94 (missile types)
    t = _t79_107(game).get(str(table_id)) or {}
    entries = list(t.get("entries") or [])
    if not entries:
        return "Weapon"
    # Roll within stated die range (usually 1d20)
    # We infer max hi from entries.
    hi = max(int(e.get("hi", 0)) for e in entries)
    e = _table_entry_by_die_roll(game, str(table_id), 1, hi) or entries[-1]
    return str(e.get("result") or "Weapon")


def _roll_armor_name_from_table(game) -> str:
    # Table 92: Magic armor types
    t = _t79_107(game).get("92") or {}
    entries = list(t.get("entries") or [])
    if not entries:
        return "Armor"
    hi = max(int(e.get("hi", 0)) for e in entries)
    e = _table_entry_by_die_roll(game, "92", 1, hi) or entries[-1]
    return str(e.get("result") or "Armor")


def _roll_remarkable_item_from_table98_index(game, idx: int) -> dict[str, Any]:
    e = _table_entry_by_index(game, "98", idx) or {"result": "Remarkable item"}
    res = str(e.get("result") or "Remarkable item")
    res_l = res.lower()

    # Table 98 maps into 99-107 (wands, rings, staffs, misc, cursed)
    if "lesser wand" in res_l:
        return _roll_from_named_table(game, "99", kind="wand")
    if "greater wand" in res_l:
        return _roll_from_named_table(game, "100", kind="wand")
    if "lesser ring" in res_l:
        return _roll_from_named_table(game, "101", kind="ring")
    if "greater ring" in res_l:
        return _roll_from_named_table(game, "102", kind="ring")
    if "magic staff" in res_l:
        return _roll_from_named_table(game, "103", kind="staff")
    if "lesser miscellaneous" in res_l:
        return _roll_from_named_table(game, "104", kind="misc")
    if "medium miscellaneous" in res_l:
        return _roll_from_named_table(game, "105", kind="misc")
    if "greater miscellaneous" in res_l:
        return _roll_from_named_table(game, "106", kind="misc")
    if "cursed" in res_l:
        return _roll_from_named_table(game, "107", kind="cursed")

    return {"kind": "magic", "name": res}


def _roll_from_named_table(game, table_id: str, kind: str) -> dict[str, Any]:
    t = _t79_107(game).get(str(table_id)) or {}
    entries = list(t.get("entries") or [])
    if not entries:
        return {"kind": kind, "name": f"{kind.title()} (Unknown)"}
    hi = max(int(e.get("hi", 0)) for e in entries)
    e = _table_entry_by_die_roll(game, str(table_id), 1, hi) or entries[-1]
    out = {"kind": kind, "name": str(e.get("result") or f"{kind.title()}")}
    if str(kind).strip().lower() in {"wand", "staff"}:
        # Practical first pass: charged items use deterministic random charges.
        out["charges"] = int(game.dice_rng.randint(12, 36))
    # Table 107 is explicitly cursed items.
    if str(table_id) == "107":
        out["cursed"] = True
        out["kind"] = "cursed"
    return out


def _tradeout_magic_items(*, game, tier: str) -> list[dict[str, Any]]:
    """Materialize Table 82/83/84 results into concrete items."""
    t = str(tier).lower().strip()
    r = int(game.dice_rng.randint(1, 4))
    out: list[dict[str, Any]] = []

    # Offsets for the 1d6 / 1d20 banding described in Tables 82-84.
    scroll_off = 0
    aw_off = 0
    rem_off = 0
    if t == "medium":
        scroll_off = 6
        aw_off = 6
        rem_off = 20
    elif t == "major":
        scroll_off = 12
        aw_off = 12
        rem_off = 40

    if t == "minor":
        potion_n = 1
    elif t == "medium":
        potion_n = 3
    else:
        potion_n = 6

    # 1) Potions
    if r == 1:
        for _ in range(potion_n):
            out.append(_roll_potion(game))
        return out

    # 2) Scrolls table (Table 86), index bands via 1d6(+off)
    if r == 2:
        idx = int(game.dice_rng.randint(1, 6)) + int(scroll_off)
        out.append(_roll_scroll_from_table86_index(game, idx))
        return out

    # 3) Magic armor/weapons (Table 89), index bands via 1d6(+off)
    if r == 3:
        idx = int(game.dice_rng.randint(1, 6)) + int(aw_off)
        out.append(_roll_magic_armor_weapons_from_table89_index(game, idx))
        return out

    # 4) Remarkable magic items (Table 98), index bands via 1d20(+off)
    idx = int(game.dice_rng.randint(1, 20)) + int(rem_off)
    out.append(_roll_remarkable_item_from_table98_index(game, idx))
    return out


def roll_xp_tradeout_hoard(*, game, budget_gp: int, instance_tag: str = "loot") -> LootRoll:
    """Implements S&W Complete Table 78 trade-outs.

    Procedure:
      1) Start with a gp budget (already computed, e.g., XP * (1d3+1)).
      2) For each 100/1000/5000 gp in the budget, there is a 10% chance of a trade-out.
      3) Do NOT subtract gp until all three trade-out types have been checked.
      4) If total trade-out cost exceeds available gp: MAJOR treasure (keep all gp AND items).
    """
    try:
        budget = max(0, int(budget_gp))
    except Exception:
        budget = 0

    t100 = 0
    t1k = 0
    t5k = 0
    # 10% chance per qualifying chunk.
    for _ in range(budget // 100):
        if int(game.dice_rng.randint(1, 10)) == 1:
            t100 += 1
    for _ in range(budget // 1000):
        if int(game.dice_rng.randint(1, 10)) == 1:
            t1k += 1
    for _ in range(budget // 5000):
        if int(game.dice_rng.randint(1, 10)) == 1:
            t5k += 1

    cost = 100 * t100 + 1000 * t1k + 5000 * t5k
    major = cost > budget
    coins_gp = budget if major else (budget - cost)

    gp_gems = 0
    gp_jewels = 0
    gems_n = 0
    jewels_n = 0
    magic_n = 0
    magic_items: list[dict[str, Any]] = []

    # Resolve trade-outs (100gp => minor, 1k => medium, 5k => major).
    for _ in range(t100):
        d20 = int(game.dice_rng.randint(1, 20))
        if d20 == 20:
            items = _tradeout_magic_items(game=game, tier="minor")
            for j, it in enumerate(items):
                magic_items.append(_wrap_magic_item_instance(item=it, instance_tag=f"{instance_tag}_m{len(magic_items)+j+1}"))
            magic_n += len(items)
        else:
            gems_n += 1
            gp_gems += _tradeout_gem_value(game=game, tier="minor")
    for _ in range(t1k):
        d20 = int(game.dice_rng.randint(1, 20))
        if d20 == 20:
            items = _tradeout_magic_items(game=game, tier="medium")
            for j, it in enumerate(items):
                magic_items.append(_wrap_magic_item_instance(item=it, instance_tag=f"{instance_tag}_m{len(magic_items)+j+1}"))
            magic_n += len(items)
        else:
            jewels_n += 1
            gp_jewels += _tradeout_gem_value(game=game, tier="medium")
    for _ in range(t5k):
        d20 = int(game.dice_rng.randint(1, 20))
        if d20 == 20:
            items = _tradeout_magic_items(game=game, tier="major")
            for j, it in enumerate(items):
                magic_items.append(_wrap_magic_item_instance(item=it, instance_tag=f"{instance_tag}_m{len(magic_items)+j+1}"))
            magic_n += len(items)
        else:
            jewels_n += 1
            gp_jewels += _tradeout_gem_value(game=game, tier="major")

    return LootRoll(
        table_id="XP_TRADEOUTS",
        gp_from_coins=int(coins_gp),
        gp_from_gems=int(gp_gems),
        gp_from_jewelry=int(gp_jewels),
        gems_count=int(gems_n),
        jewelry_count=int(jewels_n),
        magic_count=int(magic_n),
        magic_items=magic_items,
        tradeouts_100=int(t100),
        tradeouts_1000=int(t1k),
        tradeouts_5000=int(t5k),
        major_treasure=bool(major),
    )


def magic_item_to_loot_row(item: dict[str, Any], *, identify_magic: bool = False) -> dict[str, Any]:
    """Convert a rolled runtime magic item dict into a LootPool-compatible row.

    This is a thin bridge from `roll_xp_tradeout_hoard(...).magic_items` into the
    existing loot pool runtime (`add_generated_treasure_to_pool`).
    """
    src = dict(item or {})
    kind_raw = str(src.get("kind") or "magic").strip().lower()
    true_name = str(src.get("true_name") or src.get("name") or "Unknown Magic Item")
    identified = bool(src.get("identified", False)) or bool(identify_magic)
    shown_name = str(true_name if identified else (src.get("name") or _generic_unidentified_name(kind_raw)))

    # Normalize runtime kinds into loot-pool categories used by item templates.
    kind = kind_raw
    if kind in {"scroll_cursed", "scroll_protection"}:
        kind = "scroll"
    if kind in {"weapon_missile"}:
        kind = "weapon"
    if kind in {"cursed", "item_cursed", "misc"}:
        kind = "relic"

    md = dict(src.get("metadata") or {})
    if src.get("charges") is not None:
        md["charges"] = int(src.get("charges") or 0)
    if src.get("cursed") is not None:
        md["cursed"] = bool(src.get("cursed"))
    if src.get("bonus") is not None:
        md["magic_bonus"] = int(src.get("bonus") or 0)
    if src.get("spells") is not None:
        md["spells"] = list(src.get("spells") or [])

    return {
        "name": shown_name,
        "true_name": true_name,
        "kind": str(kind or "relic"),
        "quantity": max(1, int(src.get("quantity", 1) or 1)),
        "identified": bool(identified),
        "cursed": bool(src.get("cursed", False)),
        "charges": src.get("charges"),
        "metadata": md,
    }


def loot_rows_from_loot_roll(*, roll: LootRoll, identify_magic: bool = False) -> tuple[int, list[dict[str, Any]]]:
    """Translate a `LootRoll` into (coins_gp, loot_rows) for the loot pool.

    Rules remain intentionally transparent:
    - coin/gem/jewelry gp totals become coins in this first pass
    - materialized magic items are emitted as explicit loot rows
    """
    coins_gp = int(max(0, int(getattr(roll, "gp_total", 0) or 0)))
    rows: list[dict[str, Any]] = []
    for it in list(getattr(roll, "magic_items", []) or []):
        if not isinstance(it, dict):
            continue
        rows.append(magic_item_to_loot_row(it, identify_magic=identify_magic))
    return coins_gp, rows


def add_loot_roll_to_pool(*, pool: Any, roll: LootRoll, identify_magic: bool = False) -> tuple[int, list[Any]]:
    """Convenience bridge from treasure runtime rolls into `LootPool`."""
    from .loot_pool import add_generated_treasure_to_pool

    gp, rows = loot_rows_from_loot_roll(roll=roll, identify_magic=identify_magic)
    return add_generated_treasure_to_pool(pool, gp=gp, items=rows, identify_magic=identify_magic)


def choose_hoard_table_for_encounter(*, max_cl: int) -> str:
    """Map an encounter CL to a hoard table bucket.

    This is a pragmatic pacing bridge until monster-linked treasure types exist.
    """
    cl = int(max(1, int(max_cl)))
    if cl <= 3:
        return "HOARD_A"
    if cl <= 6:
        return "HOARD_B"
    return "HOARD_C"

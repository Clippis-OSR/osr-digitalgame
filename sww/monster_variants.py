from __future__ import annotations

"""Variant monster resolution (P4.9.2.6.4).

This module deterministically resolves monsters whose Challenge Level / XP are
*derived* from variant parameters rather than fixed in the statblock.

Primary use case: standard-colored dragons.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


from .data import load_json
from .dice import Dice


def _xp_for_cl(cl: int, table_77: dict[str, Any]) -> int:
    """Table 77 lookup with 16+ increment rule."""
    if cl <= 0:
        return 0
    base = table_77.get(str(cl))
    if base is not None:
        return int(base)
    # 16+ rule
    inc = int(table_77.get("16_plus_increment", 300))
    if cl < 16:
        # Shouldn't happen (missing key), but be defensive.
        return int(table_77.get("15", 0))
    base16 = int(table_77.get("16", table_77.get("15", 0) + inc))
    return base16 + (cl - 16) * inc


def _weighted_choice_index(rng: Any, weights: List[int]) -> int:
    """Return index in [0..len(weights)-1] using integer weights."""
    if not weights:
        raise ValueError("weights must be non-empty")
    total = sum(int(w) for w in weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive integer")
    r = rng.randint(1, total)
    acc = 0
    for i, w in enumerate(weights):
        acc += int(w)
        if r <= acc:
            return i
    return len(weights) - 1


def resolve_variant_monster(
    monster_def: dict[str, Any],
    *,
    rng: Any,
    desired_cl: int | None = None,
    xp_tables: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve a monster with cl_mode='derived' into a deterministic instance.

    Returns a resolved payload containing variant parameters, derived CL and XP.
    If the monster is not a supported derived variant, returns None.
    """

    if (monster_def.get("cl_mode") or "").lower() != "derived":
        return None
    variant = monster_def.get("variant") or {}
    kind = (variant.get("kind") or "").strip().lower()
    if kind == "dragon":
        return _resolve_dragon(monster_def, rng=rng, xp_tables=xp_tables)
    if kind == "dragon_turtle":
        return _resolve_dragon_turtle(monster_def, rng=rng, xp_tables=xp_tables)
    if kind == "demon_category":
        candidates = list((variant.get("candidates") or []))
        if not candidates:
            return None
        # If desired_cl provided, choose the candidate whose CL is closest (tie-breaker: lexicographic id).
        if desired_cl is not None and "candidates_with_cl" in variant:
            cwc = list((variant.get("candidates_with_cl") or []))
            if cwc:
                target = int(desired_cl)
                def key(item):
                    cid = str(item.get("monster_id") or "")
                    ccl = int(item.get("cl") or 0)
                    return (abs(ccl - target), ccl, cid)
                best = min(cwc, key=key)
                chosen = str(best.get("monster_id") or candidates[0])
            else:
                chosen = rng.choice(sorted([str(c) for c in candidates]))
        else:
            chosen = rng.choice(sorted([str(c) for c in candidates]))
        # CL/XP are represented by this bucket monster's own derived_cl/xp fields.
        cl = int(monster_def.get("challenge_level") or monster_def.get("cl_range", {}).get("min") or 0)
        xp = int(monster_def.get("xp") or 0)
        return {"kind": kind, "params": {"resolved_monster_id": chosen}, "derived_cl": cl, "derived_xp": xp}


    # Generic HD→CL derived monsters, used for Hydra/Shambling Mound/etc.
    if kind == "hd_plus_offset":
        hd_min = int(variant.get("hd_min", 1))
        hd_max = int(variant.get("hd_max", hd_min))
        cl_off = int(variant.get("cl_offset", 0))
        if desired_cl is not None:
            hd = max(hd_min, min(hd_max, int(desired_cl) - cl_off))
        else:
            hd = rng.randint(hd_min, hd_max)
        cl = hd + cl_off
        xp_tables = xp_tables or load_json("xp_tables_76_77.json")
        t77 = dict(xp_tables.get("table_77_xp_by_cl", {}) or {})
        xp = _xp_for_cl(int(cl), t77)
        return {"kind": kind, "params": {"hit_dice": hd, "cl_offset": cl_off}, "derived_cl": cl, "derived_xp": xp}

    if kind == "hd_options_plus_offset":
        opts = [int(x) for x in (variant.get("hd_options") or [])]
        if not opts:
            return None
        cl_off = int(variant.get("cl_offset", 0))
        if desired_cl is not None:
            target_hd = int(desired_cl) - cl_off
            # Choose closest option; tie-breaker = lower HD (deterministic).
            opts_sorted = sorted(opts)
            hd = min(opts_sorted, key=lambda o: (abs(o - target_hd), o))
        else:
            opts_sorted = sorted(opts)
            hd = rng.choice(opts_sorted)
        cl = hd + cl_off
        xp_tables = xp_tables or load_json("xp_tables_76_77.json")
        t77 = dict(xp_tables.get("table_77_xp_by_cl", {}) or {})
        xp = _xp_for_cl(int(cl), t77)
        return {"kind": kind, "params": {"hit_dice": hd, "cl_offset": cl_off}, "derived_cl": cl, "derived_xp": xp}



    # Discrete HD options mapped to explicit CL values (e.g., Elementals).
    if kind == "hd_to_cl_map":
        opts = [int(x) for x in (variant.get("hd_options") or [])]
        hd_to_cl = dict(variant.get("hd_to_cl") or {})
        if not opts or not hd_to_cl:
            return None
        # Choose HD based on desired_cl if provided; otherwise choose among options uniformly.
        if desired_cl is not None:
            # Find exact CL match if possible, else closest CL.
            target = int(desired_cl)
            def cl_for_hd(h):
                return int(hd_to_cl.get(str(h), hd_to_cl.get(h, h)))
            opts_sorted = sorted(opts)
            hd = min(opts_sorted, key=lambda h: (abs(cl_for_hd(h)-target), cl_for_hd(h), h))
        else:
            hd = rng.choice(sorted(opts))
        cl = int(hd_to_cl.get(str(hd), hd_to_cl.get(hd, hd)))
        xp = _xp_for_cl(int(cl), dict((xp_tables or load_json("xp_tables_76_77.json")).get("table_77_xp_by_cl", {}) or {}))
        return {"kind": kind, "params": {"hit_dice": int(hd)}, "derived_cl": int(cl), "derived_xp": int(xp)}
    return None


def _resolve_dragon(
    monster_def: dict[str, Any],
    *,
    rng: Any,
    xp_tables: dict[str, Any] | None,
) -> dict[str, Any]:
    xp_tables = xp_tables or load_json("xp_tables_76_77.json")
    t76 = dict(xp_tables.get("table_76_cl_modifiers", {}) or {})
    t77 = dict(xp_tables.get("table_77_xp_by_cl", {}) or {})

    variant = dict(monster_def.get("variant") or {})
    hd_lo, hd_hi = variant.get("hd_range") or [None, None]
    if hd_lo is None or hd_hi is None:
        raise ValueError(f"Dragon missing hd_range: {monster_def.get('monster_id')}")
    hd = rng.randint(int(hd_lo), int(hd_hi))

    # Age category roll (bell curve weights provided in data).
    ages: List[dict[str, Any]] = list(variant.get("age_categories") or [])
    weights: List[int] = list((variant.get("age_distribution") or {}).get("weights") or [])
    if not ages:
        raise ValueError(f"Dragon missing age_categories: {monster_def.get('monster_id')}")
    if weights and len(weights) != len(ages):
        raise ValueError(
            f"Dragon age weights length mismatch: {len(weights)} != {len(ages)} for {monster_def.get('monster_id')}"
        )
    if not weights:
        weights = [1 for _ in ages]
    age_idx = _weighted_choice_index(rng, weights)
    age = ages[age_idx]

    hp = int(hd) * int(age.get("hp_per_hd", 1))
    breath_max = int(hd) * int(age.get("breath_dmg_per_hd", 1))

    # Breath mode selection (gold dragon has modes).
    breath_spec: dict[str, Any] = dict(variant.get("breath") or {})
    chosen_breath: dict[str, Any] | None = None
    if "modes" in breath_spec:
        modes = list(breath_spec.get("modes") or [])
        if not modes:
            chosen_breath = None
        else:
            if bool(breath_spec.get("choose_at_spawn", True)):
                chosen_breath = dict(rng.choice(modes))
            else:
                # If we ever support per-use, we can re-roll in combat;
                # for now store the modes.
                chosen_breath = {"modes": modes}
    else:
        chosen_breath = dict(breath_spec) if breath_spec else None

    # Talk + spellcasting
    talks = rng.random() < float(variant.get("talk_pct", 0.0) or 0.0)
    spell_block = dict(variant.get("spellcasting_if_talks") or {})
    spell_triggers = False
    spells_resolved: list[dict[str, Any]] = []
    if talks and spell_block:
        spell_triggers = rng.random() < float(spell_block.get("pct", 0.0) or 0.0)
        if spell_triggers:
            d = Dice(rng=rng)
            for sp in list(spell_block.get("spells") or []):
                expr = str(sp.get("count") or "1")
                cnt = d.roll(expr).total
                spells_resolved.append(
                    {
                        "class": sp.get("class"),
                        "level": int(sp.get("level", 1)),
                        "count": int(cnt),
                        "count_expr": expr,
                    }
                )

    # Derived CL using Table 76-style tags.
    # Base CL = whole HD.
    cl = int(hd)
    cl_mods: Dict[str, int] = {}

    # Flies (all these dragons do).
    flies_mod = int(t76.get("flies_or_breathes_water", 1))
    cl += flies_mod
    cl_mods["flies_or_breathes_water"] = flies_mod

    # Breath weapon threshold.
    if breath_max <= 25:
        bmod = int(t76.get("breath_weapon_max_25_or_below", 1))
        cl_mods["breath_weapon"] = bmod
        cl += bmod
    else:
        bmod = int(t76.get("breath_weapon_max_26_or_more", 2))
        cl_mods["breath_weapon"] = bmod
        cl += bmod

    # Spellcasting modifier: applies ONLY if spellcasting triggers.
    if spell_triggers:
        # Determine the max spell level the dragon actually gets (from bundle).
        max_lvl = 0
        for sp in spells_resolved:
            max_lvl = max(max_lvl, int(sp.get("level", 0) or 0))
        # Tiered per Table 76 encoding.
        if max_lvl >= 5:
            smod = int(t76.get("multiple_spells_lvl5_or_above", 3))
        elif max_lvl >= 3:
            smod = int(t76.get("multiple_spells_lvl3_or_above", 2))
        else:
            smod = int(t76.get("multiple_spells_lvl2_or_lower", 1))
        cl_mods["spellcasting_multiple_spells"] = smod
        cl += smod

    xp = _xp_for_cl(cl, t77)

    # Dragon treasure rule: gold piece value of treasure is 4x XP.
    treasure_gp_value = int(xp) * 4

    resolved: dict[str, Any] = {
        "kind": "dragon",
        "hd": int(hd),
        "age": {
            "id": int(age.get("id", age_idx + 1)),
            "name": age.get("name"),
            "hp_per_hd": int(age.get("hp_per_hd", 1)),
            "breath_dmg_per_hd": int(age.get("breath_dmg_per_hd", 1)),
        },
        "hp": int(hp),
        "breath": {
            **({} if chosen_breath is None else dict(chosen_breath)),
            "max_damage": int(breath_max),
            "uses_per_day": int(variant.get("breath_uses_per_day", 3) or 3),
        },
        "talks": bool(talks),
        "spellcasting": {
            "triggers": bool(spell_triggers),
            "spells": spells_resolved,
        },
        "cl": int(cl),
        "xp": int(xp),
        "cl_mods": cl_mods,
        "treasure_gp_value": int(treasure_gp_value),
    }
    return resolved


def _resolve_dragon_turtle(
    monster_def: dict[str, Any],
    *,
    rng: Any,
    xp_tables: dict[str, Any] | None,
) -> dict[str, Any]:
    xp_tables = xp_tables or load_json("xp_tables_76_77.json")
    t77 = dict(xp_tables.get("table_77_xp_by_cl", {}) or {})

    variant = dict(monster_def.get("variant") or {})
    hd_lo, hd_hi = variant.get("hd_range") or [None, None]
    if hd_lo is None or hd_hi is None:
        raise ValueError(f"Dragon turtle missing hd_range: {monster_def.get('monster_id')}")
    hd = rng.randint(int(hd_lo), int(hd_hi))

    hd_to_cl = dict(variant.get("hd_to_cl") or {})
    cl = int(hd_to_cl.get(str(hd), hd_to_cl.get(hd, hd)))
    xp = _xp_for_cl(int(cl), t77)

    return {
        "kind": "dragon_turtle",
        "hd": int(hd),
        "cl": int(cl),
        "xp": int(xp),
    }

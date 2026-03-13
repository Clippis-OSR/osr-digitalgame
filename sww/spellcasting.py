from __future__ import annotations

from typing import Any, Iterable

from .rules import saving_throw, saving_throw_detail
from .status_lifecycle import apply_status
from .item_state_spells import apply_identify_to_item, apply_remove_curse_to_item
from .item_templates import item_is_cursed



def _in_battle(game: Any) -> bool:
    return bool(getattr(game, "_battle_buffer_active", False))


def _be(game: Any, event_kind: str, **data: Any) -> None:
    be = getattr(game, "battle_evt", None)
    if callable(be):
        try:
            be(event_kind, **data)
        except Exception:
            pass


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _infer_spell_tags(spell_name: str) -> set[str]:
    """Heuristic spell tagging to support category immunities.

    We keep this intentionally simple and name-based so the core dataset does not
    need to be fully re-tagged immediately.
    """
    n = _norm(spell_name)
    tags: set[str] = set()

    # Elements
    if "fire" in n or "flame" in n or n in {"fireball", "burning hands"}:
        tags.add("fire")
    if "lightning" in n or "electric" in n:
        tags.add("electric")
    if "cold" in n or "ice" in n or "frost" in n:
        tags.add("cold")
    if "acid" in n:
        tags.add("acid")

    # Mind/enchantment patterns
    if any(k in n for k in [
        "charm",
        "sleep",
        "hold ",
        "fear",
        "confusion",
        "suggestion",
        "hypnot",
        "emotion",
        "geas",
        "quest",
        "dominate",
        "command",
        "feeblemind",
    ]):
        tags.add("mind_affecting")
    if any(k in n for k in ["charm", "sleep", "hold ", "suggestion", "geas", "quest", "dominate"]):
        tags.add("enchantment")

    # Illusion-ish patterns
    if any(k in n for k in ["phantas", "illusion", "mirror image", "invisibility", "shadow"]):
        tags.add("illusion")

    # Utility
    if "dispel" in n:
        tags.add("dispel")

    return tags


def _emit_save(
    game: Any,
    dice: Any,
    target: Any,
    *,
    spell_name: str,
    tags: set[str],
    bonus: int = 0,
) -> bool:
    """Roll a save, emit a detailed SAVE_THROW battle event, return ok."""
    try:
        ok, roll, total, need, mod, used_tags = saving_throw_detail(dice, target, bonus=bonus, tags=set(tags))
    except Exception:
        ok = saving_throw(dice, target, bonus=bonus, tags=set(tags))
        roll = None
        total = None
        need = int(getattr(target, "save", 15) or 15)
        mod = int(bonus or 0)
        used_tags = set(tags)

    _be(
        game,
        "SAVE_THROW",
        unit_id=getattr(target, "name", str(target)),
        save="spell",
        spell=str(spell_name),
        tags=sorted(list(used_tags)),
        mod=mod,
        roll=roll,
        total=total,
        need=need,
        result=("passed" if ok else "failed"),
    )
    return bool(ok)


def _get_spell_tags(game: Any, spell_name: str) -> set[str]:
    """Return tags for a spell.

    Uses optional tags from spells.json when present, then augments with
    heuristic inference.
    """
    tags: set[str] = set()
    try:
        sp = None
        if hasattr(game, "content"):
            if hasattr(game.content, "get_spell_by_name"):
                sp = game.content.get_spell_by_name(spell_name)
            elif hasattr(game.content, "get_spell"):
                sp = game.content.get_spell(spell_name)
        if isinstance(sp, dict):
            raw = sp.get("tags")
            if isinstance(raw, list):
                tags.update({_norm(t) for t in raw if _norm(t)})
        else:
            raw = getattr(sp, "tags", None)
            if isinstance(raw, list):
                tags.update({_norm(t) for t in raw if _norm(t)})
    except Exception:
        pass
    tags.update(_infer_spell_tags(spell_name))
    return tags


def _check_spell_immunity(*, game: Any, target: Any, spell_name: str) -> bool:
    """Return True if the target is outright immune to the spell.

    This is distinct from Magic Resistance (a % roll). Some creatures (notably
    constructs like golems) are traditionally *immune to most spells* except
    for a small set of explicit interactions.

    Damage-profile fields:
      - spell_immunity_all: bool (immune to all spells unless exempt)
      - spell_immunity_exempt_spells: [str] (case-insensitive spell-name list)
      - spell_immunity_tags: [str] (immune if the spell has any of these tags)
    """
    prof = getattr(target, "damage_profile", None) or {}
    try:
        immune_all = bool(prof.get("spell_immunity_all", False))
    except Exception:
        immune_all = False
    if not immune_all:
        return False

    try:
        exempt = prof.get("spell_immunity_exempt_spells", [])
        if isinstance(exempt, (list, tuple, set)):
            ex = {str(x).strip().lower() for x in exempt if str(x).strip()}
        else:
            ex = set()
    except Exception:
        ex = set()

    if ex and _norm(spell_name) in ex:
        return False

    # Immune.
    try:
        game.events.emit(
            "spell_immunity",
            {"target": getattr(target, "name", ""), "spell": str(spell_name), "immune_all": True},
        )
    except Exception:
        pass
    try:
        _be(game, "SPELL_IMMUNE", target_id=getattr(target, "name", ""), spell=str(spell_name))
        game.ui.log(f"{target.name} is unaffected by the spell!")
    except Exception:
        pass
    return True


def _check_spell_tag_immunity(*, game: Any, target: Any, spell_name: str) -> bool:
    """Return True if target is immune to the spell due to tag/category rules."""
    prof = getattr(target, "damage_profile", None) or {}
    raw = prof.get("spell_immunity_tags") or []
    if not isinstance(raw, (list, tuple, set)) or not raw:
        return False

    try:
        exempt = prof.get("spell_immunity_exempt_spells", [])
        if isinstance(exempt, (list, tuple, set)):
            ex = {_norm(x) for x in exempt if _norm(x)}
        else:
            ex = set()
    except Exception:
        ex = set()
    if ex and _norm(spell_name) in ex:
        return False

    required = {_norm(t) for t in raw if _norm(t)}
    if not required:
        return False
    spell_tags = _get_spell_tags(game, spell_name)
    hit = spell_tags.intersection(required)
    if not hit:
        return False

    try:
        game.events.emit(
            "spell_immunity",
            {"target": getattr(target, "name", ""), "spell": str(spell_name), "immune_tags": sorted(hit)},
        )
    except Exception:
        pass
    try:
        game.ui.log(f"{target.name} is unaffected by the spell! ({', '.join(sorted(hit))})")
    except Exception:
        pass
    return True


def _check_magic_resistance(*, game: Any, dice: Any, target: Any, spell_name: str) -> bool:
    """Return True if target's magic resistance negates the spell.

    S&W-style magic resistance is a percentile chance to ignore spells, distinct
    from saving throws. We apply it *before* any saving throw.
    """
    try:
        prof = getattr(target, "damage_profile", None) or {}
        mr = int(prof.get("magic_resistance_pct", 0) or 0)
    except Exception:
        mr = 0
    if mr <= 0:
        return False

    # Optional exceptions: some creatures (e.g., flesh golems) have broad MR but
    # specific spells should still apply to allow classic interactions.
    try:
        exempt = prof.get("magic_resistance_exempt_spells", [])
        if isinstance(exempt, (list, tuple, set)):
            ex = {str(x).strip().lower() for x in exempt if str(x).strip()}
        else:
            ex = set()
    except Exception:
        ex = set()
    if ex and str(spell_name).strip().lower() in ex:
        return False

    roll = int(dice.d(100))
    resisted = roll <= mr
    try:
        game.events.emit(
            "magic_resistance_check",
            {"target": getattr(target, "name", ""), "spell": str(spell_name), "roll": int(roll), "pct": int(mr), "resisted": bool(resisted)},
        )
    except Exception:
        pass
    if resisted:
        try:
            _be(game, "MAGIC_RESISTED", target_id=getattr(target, "name", ""), spell=str(spell_name), roll=int(roll), pct=int(mr))
            game.ui.log(f"{target.name} resists the spell entirely! (MR {mr}%)")
        except Exception:
            pass
        return True
    return False


def spell_level_num(spell_level_field: str) -> int:
    """Parse "1st level" / "2nd level" etc into an integer."""
    s = (spell_level_field or "").lower()
    for n in range(1, 10):
        if f"{n}st" in s or f"{n}nd" in s or f"{n}rd" in s or f"{n}th" in s:
            return n
    # Some extracted datasets might use "Level 1" or similar.
    for n in range(1, 10):
        if f"level {n}" in s:
            return n
    return 1


def spell_level_for_ident(*, content: Any, spell_ident: str) -> int:
    """Resolve a spell's level from either a spell_id or a display name."""
    try:
        sp = content.get_spell(spell_ident) if hasattr(content, "get_spell") else None
    except Exception:
        sp = None
    if sp is None:
        # Best-effort: some callers may still pass names.
        try:
            sp = content.get_spell_by_name(spell_ident) if hasattr(content, "get_spell_by_name") else None
        except Exception:
            sp = None
    if sp is None:
        return 1
    try:
        return spell_level_num(getattr(sp, "spell_level", "1st"))
    except Exception:
        return 1


def available_spells_for_prepare_by_level(*, caster: Any, content: Any, spell_level: int) -> list[str]:
    """Return spells the caster may prepare for a specific spell level.

    Returns a list of spell *names* (not ids) for UI selection.
    """
    spell_level = int(spell_level)
    cls = str(getattr(caster, "cls", "")).strip().lower()
    if spell_level <= 0:
        return []

    if cls == "magic-user":
        known = list(getattr(caster, "spells_known", []) or [])
        out: list[str] = []
        for nm in known:
            try:
                sp = content.get_spell(nm) if hasattr(content, "get_spell") else None
            except Exception:
                sp = None
            if sp is None:
                continue
            if spell_level_num(getattr(sp, "spell_level", "1st")) == spell_level:
                out.append(getattr(sp, "name", nm) or nm)
        return sorted(set(out))

    if cls == "cleric":
        out: list[str] = []
        try:
            for sp in content.list_spells_for_class("Cleric"):
                if spell_level_num(getattr(sp, "spell_level", "1st")) == spell_level:
                    out.append(sp.name)
        except Exception:
            out = []
        return sorted(set(out))

    # Extend later (druid, ranger) as they become spellcasters.
    return []


def available_spells_for_prepare(*, caster: Any, content: Any) -> list[str]:
    """Return the set of spells a caster may prepare right now."""
    cls = str(getattr(caster, "cls", "")).strip().lower()
    slots = dict(getattr(caster, "spell_slots", {}) or {})
    if not slots:
        return []
    max_slvl = max(int(k) for k in slots.keys())

    # Magic-User prepares from known spells.
    if cls == "magic-user":
        known = list(getattr(caster, "spells_known", []) or [])
        # Filter by level if we can resolve spell levels.
        out: list[str] = []
        for nm in known:
            sp = content.get_spell(nm) if hasattr(content, "get_spell") else None
            if sp is None:
                out.append(nm)
                continue
            if spell_level_num(getattr(sp, "spell_level", "1st")) <= max_slvl:
                out.append(nm)
        return sorted(set(out))

    # Clerics prepare from the whole list up to spell level.
    if cls == "cleric":
        out: list[str] = []
        try:
            for sp in content.list_spells_for_class("Cleric"):
                if spell_level_num(getattr(sp, "spell_level", "1st")) <= max_slvl:
                    out.append(sp.name)
        except Exception:
            out = []
        return sorted(set(out))

    return []


def prepare_spell_list(*, chosen: list[str], caster: Any, content: Any) -> list[str]:
    """Normalize a chosen prepared list to fit caster's slots.

    We allow duplicates, but enforce the total and per-spell-level slot counts.
    """
    slots = {int(k): int(v) for k, v in dict(getattr(caster, "spell_slots", {}) or {}).items()}
    if not slots:
        return []

    def slvl(name: str) -> int:
        sp = content.get_spell(name) if hasattr(content, "get_spell") else None
        return spell_level_num(getattr(sp, "spell_level", "1st")) if sp else 1

    remaining = dict(slots)
    out: list[str] = []
    for nm in chosen:
        lv = slvl(nm)
        if remaining.get(lv, 0) > 0:
            remaining[lv] -= 1
            out.append(nm)
    return out


# --------------------
# Spell effect helpers
# --------------------


def _damage_d6_per_level(dice: Any, caster_level: int, cap: int = 10) -> int:
    n = max(1, min(int(caster_level), int(cap)))
    return sum(dice.d(6) for _ in range(n))


def _party_item_spell_candidates(
    game: Any,
    *,
    only_unidentified: bool = False,
    only_cursed: bool = False,
    ally_pool: Iterable[Any] | None = None,
) -> list[tuple[Any, Any]]:
    candidates: list[tuple[Any, Any]] = []
    pool = list(ally_pool) if ally_pool is not None else list(getattr(game.party, "members", []) or [])
    for actor in pool:
        if not hasattr(actor, "inventory"):
            continue
        actor.ensure_inventory_initialized()
        for item in (actor.inventory.items or []):
            if only_unidentified and bool(getattr(item, "identified", True)):
                continue
            if only_cursed:
                md = dict(getattr(item, "metadata", {}) or {})
                if (not bool(md.get("cursed", False))) and (not bool(item_is_cursed(item))):
                    continue
            candidates.append((actor, item))
    return candidates


def apply_spell_in_combat(
    *,
    game: Any,
    caster: Any,
    spell_name: str,
    foes: list[Any],
    allies: list[Any] | None = None,
    forced_foe: Any | None = None,
    forced_ally: Any | None = None,
) -> bool:
    """Apply spell effects for core spells.

    This is intentionally centralized so future classes can share mechanics.
    """
    dice = game.dice
    s = str(spell_name).strip().lower()
    lvl = int(getattr(caster, "level", 1) or 1)

    living_foes = [f for f in foes if f.hp > 0 and 'fled' not in (getattr(f, 'effects', []) or [])]

    _be(game, "SPELL_CAST", caster_id=getattr(caster, 'name', ''), spell=str(spell_name))

    def choose_foe(prompt: str) -> Any | None:
        if forced_foe is not None:
            if forced_foe in living_foes:
                return forced_foe
            return None
        if not living_foes:
            return None
        try:
            if hasattr(game, '_combat_board_pick_unit') and bool(getattr(game, '_combat_board_runtime', None)):
                picked = game._combat_board_pick_unit(prompt, living_foes, selected_actor=caster, selected_target=living_foes[0], phase='Planning')
                if picked is not None:
                    return picked
        except Exception:
            pass
        labels = [f"{f.name} (HP {f.hp})" for f in living_foes]
        t = game.ui.choose(prompt, labels)
        return living_foes[t]

    def choose_ally(prompt: str) -> Any | None:
        if forced_ally is not None:
            return forced_ally
        ally_pool = list(allies) if allies is not None else [a for a in game.party.living()]
        allies_live = [a for a in ally_pool if getattr(a, 'hp', 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or [])]
        if not allies_live:
            return None
        try:
            if hasattr(game, '_combat_board_pick_unit') and bool(getattr(game, '_combat_board_runtime', None)):
                picked = game._combat_board_pick_unit(prompt, allies_live, selected_actor=caster, selected_target=allies_live[0], phase='Planning')
                if picked is not None:
                    return picked
        except Exception:
            pass
        labels = [f"{a.name} (HP {a.hp}/{a.hp_max})" for a in allies_live]
        t = game.ui.choose(prompt, labels)
        return allies_live[t]

    # Bless is not castable in combat in S&W Complete.
    if s == "bless":
        game.ui.log("Bless must be cast before combat.")
        return False


    # ---- Purification / Restoration ----
    if s in ("cure disease", "neutralize poison"):
        target = choose_ally(f"{spell_name} on who?")
        if not target:
            game.ui.log("No target.")
            return
        # Remove matching effect instances (P4.10.12)
        try:
            game.effects_mgr.ensure_migrated(target)
            insts = getattr(target, "effect_instances", None)
            if isinstance(insts, dict):
                removed = 0
                for eid, inst in list(insts.items()):
                    if not isinstance(inst, dict):
                        continue
                    kind = str(inst.get("kind") or "").lower()
                    if s == "cure disease" and kind == "disease":
                        game.effects_mgr.remove(target, eid, reason="cured", ctx={"spell": spell_name})
                        removed += 1
                    if s == "neutralize poison" and kind == "poison":
                        game.effects_mgr.remove(target, eid, reason="neutralized", ctx={"spell": spell_name})
                        removed += 1
                if removed <= 0:
                    game.ui.log(f"Nothing to cleanse on {target.name}.")
                else:
                    game.ui.log(f"{target.name} is cleansed. ({removed} effect(s) removed.)")
            else:
                game.ui.log("Nothing to cleanse.")
        except Exception:
            game.ui.log("The spell fizzles.")
            return False
        return True
    # ---- Magic-User staples ----
    if s == "sleep":
        # No save. Affect 2d6 HD of creatures, lowest HD first.
        hd_budget = dice.d(6) + dice.d(6)
        affected = 0
        for f in sorted(living_foes, key=lambda x: int(getattr(x, 'hd', 1) or 1)):
            if _check_spell_immunity(game=game, target=f, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=f, spell_name=spell_name):
                continue
            if _check_magic_resistance(game=game, dice=dice, target=f, spell_name=spell_name):
                continue
            hd = int(getattr(f, 'hd', 1) or 1)
            if hd <= hd_budget and hd > 0:
                hd_budget -= hd
                apply_status(f, "asleep", 6, mode="max")
                _be(game, "EFFECT_APPLIED", tgt=getattr(f, "name", str(f)), kind="asleep")
                affected += 1
        _be(game, "AOE_AFFECTS", spell=spell_name, count=int(affected), kind="sleep")
        game.ui.log(f"Sleep affects {affected} foe(s).")
        return True

    if s == "magic missile":
        target = choose_foe("Magic Missile hits who?")
        if not target:
            game.ui.log("No target.")
            return
        if _check_spell_immunity(game=game, target=target, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=target, spell_name=spell_name):
            return True
        if _check_magic_resistance(game=game, dice=dice, target=target, spell_name=spell_name):
            return True
        # S&W-style Magic Missile: auto-hit; damage is 1d4+1 per missile.
        # Fixture compatibility expects multiple missiles based on caster level (capped at 5).
        try:
            lvl = int(getattr(caster, 'level', 1) or 1)
        except Exception:
            lvl = 1
        missiles = max(1, min(5, lvl))
        total = 0
        for _ in range(missiles):
            dmg = int(dice.d(4)) + 1
            total += dmg
            try:
                from .damage import DamagePacket, apply_damage_packet
                _ = apply_damage_packet(
                    game=game,
                    source=caster,
                    target=target,
                    packet=DamagePacket(amount=int(dmg), damage_type='force', magical=True),
                    ctx={'spell': spell_name, 'combat_foes': list(foes or [])},
                )
            except Exception:
                target.hp = max(-1, target.hp - int(dmg))
                try:
                    getattr(game, '_notify_damage')(target, int(dmg), damage_types={'force'})
                except Exception:
                    pass
        _be(game, "DAMAGE", target_id=target.name, amount=int(total), hp=int(target.hp), hp_max=int(getattr(target,"hp_max",target.hp) or target.hp))
        game.ui.log(f"Magic Missile hits {target.name} for {int(total)} (HP {target.hp}/{target.hp_max})")
        return True

    if s in ("charm person", "hold person"):
        target = choose_foe(f"{spell_name} targets who?")
        if not target:
            game.ui.log("No target.")
            return
        if _check_spell_immunity(game=game, target=target, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=target, spell_name=spell_name):
            return True
        if _check_magic_resistance(game=game, dice=dice, target=target, spell_name=spell_name):
            return True
        st_ok = _emit_save(game, dice, target, spell_name=spell_name, tags={"spell", "magic"})
        if st_ok:
            game.ui.log(f"{target.name} resists!")
            return True
        if s == "charm person":
            apply_status(target, "charmed", 999)
            _be(game, "EFFECT_APPLIED", tgt=target.name, kind="charmed")
            if hasattr(game, '_leave_combat_actor'):
                game._leave_combat_actor(target, marker='fled')  # treated as out of fight
            else:
                effects = list(getattr(target, 'effects', []) or [])
                if 'fled' not in effects:
                    effects.append('fled')
                target.effects = effects
            game.ui.log(f"{target.name} is charmed and stops fighting!")
        else:
            apply_status(target, "held", 6, mode="max")
            _be(game, "EFFECT_APPLIED", tgt=target.name, kind="held")
            game.ui.log(f"{target.name} is held!")
        return True

    if s in ("fireball", "lightning bolt"):
        dmg = _damage_d6_per_level(dice, lvl, cap=10)
        hits = 0
        for f in list(living_foes):
            if _check_spell_immunity(game=game, target=f, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=f, spell_name=spell_name):
                hits += 1
                continue
            if _check_magic_resistance(game=game, dice=dice, target=f, spell_name=spell_name):
                hits += 1
                continue
            st_ok = _emit_save(game, dice, f, spell_name=spell_name, tags={"spell", "magic"})
            if st_ok:
                dealt = max(1, dmg // 2)
            else:
                dealt = dmg
            try:
                from .damage import DamagePacket, apply_damage_packet
                dtype = "fire" if s == "fireball" else "electric"
                apply_damage_packet(
                    game=game,
                    source=caster,
                    target=f,
                    packet=DamagePacket(amount=int(dealt), damage_type=dtype, magical=True),
                    ctx={"spell": spell_name, "combat_foes": list(foes or [])},
                )
            except Exception:
                f.hp = max(-1, f.hp - int(dealt))
                try:
                    getattr(game, "_notify_damage")(f, int(dealt), damage_types={"fire" if s == "fireball" else "electric"})
                except Exception:
                    pass
            hits += 1
        _be(game, "AOE_AFFECTS", spell=spell_name, count=int(hits), dmg=int(dmg), kind=str(s))
        game.ui.log(f"{spell_name} hits {hits} foe(s)!")
        return True

    if s == "shield":
        apply_status(caster, "shield", {"rounds": 6, "ac_bonus": 2})
        _be(game, "EFFECT_APPLIED", tgt=caster.name, kind="shield")
        game.ui.log(f"{caster.name} is protected by a shimmering shield.")
        return True

    if s == "invisibility":
        target = choose_ally("Invisibility on who?")
        if not target:
            game.ui.log("No target.")
            return
        apply_status(target, "invisible", 6)
        _be(game, "EFFECT_APPLIED", tgt=target.name, kind="invisible")
        game.ui.log(f"{target.name} fades from sight!")
        return True

    if s == "web":
        hits = 0
        for f in list(living_foes):
            if _check_spell_immunity(game=game, target=f, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=f, spell_name=spell_name):
                continue
            if _check_magic_resistance(game=game, dice=dice, target=f, spell_name=spell_name):
                continue
            st_ok = _emit_save(game, dice, f, spell_name=spell_name, tags={"spell", "magic"})
            if st_ok:
                continue
            apply_status(f, "held", 3, mode="max")
            _be(game, "EFFECT_APPLIED", tgt=f.name, kind="held")
            hits += 1
        _be(game, "AOE_AFFECTS", spell=spell_name, count=int(hits), kind="web")
        game.ui.log(f"Web entangles {hits} foe(s)!")
        return True

    if s == "identify":
        # Temporary simplification: in combat, Identify only reveals the target item's
        # known name/state (not full effects/curses).
        candidates = _party_item_spell_candidates(game, only_unidentified=True, ally_pool=allies)
        if not candidates:
            game.ui.log("No unidentified items to identify.")
            return False
        labels = [f"{getattr(a, 'name', '?')}: {getattr(i, 'name', 'Unknown item')}" for a, i in candidates]
        j = game.ui.choose("Identify which item?", labels)
        actor, item = candidates[j]
        res = apply_identify_to_item(game.party, item.instance_id, reveal_curse=False, reveal_effects=False)
        if not res.ok:
            game.ui.log("Identify fails to reveal that item.")
            return False
        game.ui.log(f"{getattr(actor, 'name', 'Someone')}'s item is identified: {getattr(item, 'name', 'item')}.")
        return True

    if s == "remove curse":
        # Temporary simplification: suppress sticky curse restriction on one item so
        # normal unequip flow can proceed.
        candidates = _party_item_spell_candidates(game, only_cursed=True, ally_pool=allies)
        if not candidates:
            game.ui.log("No cursed items are currently affecting the party.")
            return False
        labels = [f"{getattr(a, 'name', '?')}: {getattr(i, 'name', 'Unknown item')}" for a, i in candidates]
        j = game.ui.choose("Remove Curse from which item?", labels)
        actor, item = candidates[j]
        res = apply_remove_curse_to_item(game.party, item.instance_id, suppress_sticky=True)
        if not res.ok:
            game.ui.log("The curse does not yield.")
            return False
        game.ui.log(f"A cleansing force loosens the curse on {getattr(actor, 'name', 'the wielder')}'s {getattr(item, 'name', 'item')}.")
        return True

    # ---- Cleric staples ----
    if s == "cure disease":
        target = choose_ally("Cure Disease on who?")
        if not target:
            game.ui.log("No target.")
            return
        try:
            mgr = getattr(game, "effects_mgr")
            mgr.ensure_migrated(target)
            removed = mgr.remove_kind(target, "disease", reason="cure_disease")
        except Exception:
            removed = 0
        if removed > 0:
            game.ui.log(f"{target.name} is cured of disease.")
        else:
            game.ui.log(f"{target.name} has no disease to cure.")
        return True

    if s in ("cure light wounds", "cure moderate wounds"):
        target = choose_ally(f"{spell_name} heals who?")
        if not target:
            game.ui.log("No target.")
            return
        # Disease/rot may prevent magical healing.
        try:
            mods = getattr(game, "effects_mgr").query_modifiers(target)
        except Exception:
            mods = None
        if mods is not None and getattr(mods, "blocks_magical_heal", False):
            game.ui.log(f"The healing magic fails to mend {target.name} due to disease.")
            return
        if s == "cure light wounds":
            heal = dice.d(6) + 1
        else:
            heal = dice.d(8) + 2
        target.hp = min(target.hp_max, target.hp + heal)
        _be(game, "HEAL", target_id=target.name, amount=int(heal), hp=int(target.hp), hp_max=int(getattr(target,"hp_max",target.hp) or target.hp))
        game.ui.log(f"{target.name} heals {heal} HP (HP {target.hp}/{target.hp_max})")
        return True

    if s == "protection from evil":
        target = choose_ally("Protection from Evil on who?")
        if not target:
            game.ui.log("No target.")
            return
        apply_status(target, "prot_evil", {"rounds": 6, "enemy_to_hit": -2})
        _be(game, "EFFECT_APPLIED", tgt=target.name, kind="prot_evil")
        game.ui.log(f"{target.name} is protected from evil.")
        return True

    if s == "sanctuary":
        target = choose_ally("Sanctuary on who?")
        if not target:
            game.ui.log("No target.")
            return
        apply_status(target, "sanctuary", {"rounds": 6})
        _be(game, "EFFECT_APPLIED", tgt=target.name, kind="sanctuary")
        game.ui.log(f"{target.name} is under sanctuary.")
        return True

    # Fallback: single-target magical damage with save for half.
    target = choose_foe("Spell targets who?")
    if not target:
        game.ui.log("No effect.")
        return
    if _check_spell_immunity(game=game, target=target, spell_name=spell_name) or _check_spell_tag_immunity(game=game, target=target, spell_name=spell_name):
        return True
    if _check_magic_resistance(game=game, dice=dice, target=target, spell_name=spell_name):
        return True
    dmg = dice.d(6)
    st_ok = _emit_save(game, dice, target, spell_name=spell_name, tags={"spell", "magic"})
    if st_ok:
        dmg = max(1, dmg // 2)
    try:
        from .damage import DamagePacket, apply_damage_packet
        dealt = apply_damage_packet(
            game=game,
            source=caster,
            target=target,
            packet=DamagePacket(amount=int(dmg), damage_type="magic", magical=True),
            ctx={"spell": spell_name, "combat_foes": list(foes or [])},
        )
    except Exception:
        dealt = int(dmg)
        target.hp = max(-1, target.hp - dealt)
        try:
            getattr(game, "_notify_damage")(target, dealt, damage_types={"magic"})
        except Exception:
            pass
    _be(game, "DAMAGE", target_id=target.name, amount=int(dealt), hp=int(target.hp), hp_max=int(getattr(target,"hp_max",target.hp) or target.hp))
    game.ui.log(f"{spell_name} harms {target.name} for {int(dealt)} (HP {target.hp}/{target.hp_max})")
    return True


def apply_spell_out_of_combat(*, game: Any, caster: Any, spell_name: str, context: str = "dungeon") -> None:
    """Apply a subset of spells outside combat.

    This exists so multiple classes can share exploration magic as you add more classes.
    """
    s = str(spell_name).strip().lower()

    def choose_ally(prompt: str) -> Any | None:
        allies_live = [a for a in game.party.living()]
        if not allies_live:
            return None
        labels = [f"{a.name} (HP {a.hp}/{a.hp_max})" for a in allies_live]
        t = game.ui.choose(prompt, labels)
        return allies_live[t]

    # Light: sets magical light duration in dungeon turns.
    if s == "light":
        if context == "dungeon":
            # S&W: Light lasts 6 turns + 1 turn/level. We track turns.
            lvl = int(getattr(caster, "level", 1) or 1)
            dur = 6 + max(0, lvl)
            game.magical_light_turns = max(int(getattr(game, "magical_light_turns", 0) or 0), dur)
            game.light_on = True
            game.ui.log("A steady magical light fills the area.")
        else:
            game.ui.log("A magical light appears.")
        return

    if s == "identify":
        # Temporary simplification: Identify reveals item name and basic state,
        # but does not reveal curse status by default.
        candidates = _party_item_spell_candidates(game, only_unidentified=True)

        if not candidates:
            game.ui.log("No unidentified items to identify.")
            return

        labels = [f"{getattr(a, 'name', '?')}: {getattr(i, 'name', 'Unknown item')}" for a, i in candidates]
        j = game.ui.choose("Identify which item?", labels + ["Back"])
        if j == len(labels):
            return

        actor, item = candidates[j]
        res = apply_identify_to_item(game.party, item.instance_id, reveal_curse=False, reveal_effects=False)
        if not res.ok:
            game.ui.log("Identify fails to reveal that item.")
            return
        game.ui.log(f"{getattr(actor, 'name', 'Someone')}'s item is identified: {getattr(item, 'name', 'item')}.")
        return

    if s == "detect magic":
        # Mark the current room so the UI can mention it.
        try:
            room = game.dungeon_rooms.get(game.current_room_id) or None
            if isinstance(room, dict):
                room["detect_magic"] = True
        except Exception:
            pass

        # Ownership-aware path: mark pending loot-pool entries as aura-bearing
        # without forcing identification or writing directly to legacy party_items.
        try:
            for e in list(getattr(getattr(game, "loot_pool", None), "entries", []) or []):
                if getattr(e, "metadata", None) is None:
                    setattr(e, "metadata", {})
                md = dict(getattr(e, "metadata", {}) or {})
                md["aura"] = True
                setattr(e, "metadata", md)
            # Compatibility mirror for older menu surfaces.
            if hasattr(game, "_sync_legacy_party_items_from_loot_pool"):
                game._sync_legacy_party_items_from_loot_pool()
        except Exception:
            pass
        game.ui.log("You sense magical auras nearby.")
        return



    if s == "remove curse":
        # Temporary simplification: Remove Curse suppresses sticky-equip curse locks
        # on one item, allowing normal unequip. It does not rewrite template curse data.
        candidates = _party_item_spell_candidates(game, only_cursed=True)

        if candidates:
            labels = [f"{getattr(a, 'name', '?')}: {getattr(i, 'name', 'Unknown item')}" for a, i in candidates]
            j = game.ui.choose("Remove Curse from which item?", labels + ["Back"])
            if j == len(labels):
                return
            actor, item = candidates[j]
            res = apply_remove_curse_to_item(game.party, item.instance_id, suppress_sticky=True)
            if not res.ok:
                game.ui.log("The curse does not yield.")
                return
            game.ui.log(f"A cleansing force loosens the curse on {getattr(actor, 'name', 'the wielder')}'s {getattr(item, 'name', 'item')}.")
            return

        target = choose_ally("Remove Curse from who?")
        if not target:
            game.ui.log("No target.")
            return
        game.ui.log(f"A cleansing force washes over {target.name}, but no item curse is present.")
        return

    if s in {"raise dead", "resurrection"}:
        dead = [a for a in getattr(game.party, "members", []) if int(getattr(a, "hp", 0) or 0) < 0]
        if not dead:
            game.ui.log("No dead allies present.")
            return
        labels = [f"{a.name} ({a.cls})" for a in dead]
        j = game.ui.choose("Raise Dead for who?", labels + ["Back"])
        if j == len(labels):
            return
        target = dead[j]
        # S&W Complete: Raise Dead survival is a flat % chance based on CON (Table 3).
        from .rules import raise_dead_survival_pct
        con = int(getattr(target, "con", 10) or 10)
        pct = int(raise_dead_survival_pct(con))
        roll = int(game.dice.d(100))
        ok = (roll <= pct)
        if ok:
            target.hp = 1
            game.ui.log(f"{target.name} returns to life! (CON {con} ⇒ {pct}%, roll {roll})")
        else:
            game.ui.log(f"The magic fails. (CON {con} ⇒ {pct}%, roll {roll}) {target.name} remains dead.")
        return

    if s == "cure disease":
        target = choose_ally("Cure Disease on who?")
        if not target:
            game.ui.log("No target.")
            return
        try:
            mgr = getattr(game, "effects_mgr")
            mgr.ensure_migrated(target)
            removed = mgr.remove_kind(target, "disease", reason="cure_disease")
        except Exception:
            removed = 0
        if removed > 0:
            game.ui.log(f"{target.name} is cured of disease.")
        else:
            game.ui.log(f"{target.name} has no disease to cure.")
        return

    if s == "neutralize poison":
        target = choose_ally("Neutralize Poison on who?")
        if not target:
            game.ui.log("No target.")
            return
        try:
            mgr = getattr(game, "effects_mgr")
            mgr.ensure_migrated(target)
            removed = mgr.remove_kind(target, "poison", reason="neutralize_poison")
        except Exception:
            removed = 0
        if removed > 0:
            game.ui.log(f"{target.name} is cured of poison.")
        else:
            game.ui.log(f"{target.name} has no poison to neutralize.")
        return

    if s in {"cure light wounds", "cure wounds"}:
        target = choose_ally("Cure Wounds on who?")
        if not target:
            game.ui.log("No target.")
            return
        try:
            amt = int(game.dice.roll("1d6") + 1)
        except Exception:
            amt = 4
        before = int(getattr(target, "hp", 0) or 0)
        target.hp = min(int(getattr(target, "hp_max", before) or before), before + amt)
        game.ui.log(f"{target.name} recovers {target.hp - before} HP.")
        return

    if s == "cure serious wounds":
        target = choose_ally("Cure Serious Wounds on who?")
        if not target:
            game.ui.log("No target.")
            return
        try:
            amt = int(game.dice.roll("2d6") + 2)
        except Exception:
            amt = 9
        before = int(getattr(target, "hp", 0) or 0)
        target.hp = min(int(getattr(target, "hp_max", before) or before), before + amt)
        game.ui.log(f"{target.name} recovers {target.hp - before} HP.")
        return


    if s == "protection from evil":
        target = choose_ally("Protection from Evil on who?")
        if not target:
            game.ui.log("No target.")
            return
        # S&W: 6 turns (1 hour)
        apply_status(target, "prot_evil", {"turns": 6, "enemy_to_hit": -2})
        game.ui.log(f"{target.name} is protected from evil.")
        return

    if s == "sanctuary":
        target = choose_ally("Sanctuary on who?")
        if not target:
            game.ui.log("No target.")
            return
        # S&W: 2 turns (classic). If the spell data differs, we still keep a short duration.
        apply_status(target, "sanctuary", {"turns": 2})
        game.ui.log(f"{target.name} is under sanctuary.")
        return

    if s == "invisibility":
        target = choose_ally("Invisibility on who?")
        if not target:
            game.ui.log("No target.")
            return
        # S&W: 24 turns (4 hours) for invisibility.
        apply_status(target, "invisible", {"turns": 24})
        game.ui.log(f"{target.name} fades from sight!")
        return

    if s == "bless":
        # S&W: Bless lasts 6 turns (1 hour) and must be cast before combat.
        for a in game.party.living():
            apply_status(a, "bless", {"turns": 6, "to_hit": 1})
        game.ui.log("Bless grants +1 to hit to the party for 1 hour.")
        return

    game.ui.log("Nothing obvious happens.")

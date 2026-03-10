from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DamagePacket:
    """A standardized representation of damage (or healing transforms).

    The intent is that *all* HP changes flow through a single pipeline so monster
    defenses (immunity/resistance/magic-only) are centralized and deterministic.
    """

    amount: int
    damage_type: str = "physical"  # e.g. physical, fire, cold, acid, electric, poison, magic, force
    magical: bool = False
    enchantment_bonus: int = 0  # weapon bonus (e.g. +1 sword => 1)
    source_tags: set[str] = field(default_factory=set)
    # Optional weapon/material tags (e.g. "silver", "cold_iron").
    # These are used for classic OSR gates like lycanthropes requiring silver.


def _as_set_list(d: dict, key: str) -> set[str]:
    try:
        v = d.get(key, [])
        if isinstance(v, set):
            return set(v)
        if isinstance(v, list):
            return {str(x) for x in v}
        if isinstance(v, tuple):
            return {str(x) for x in v}
    except Exception:
        return set()
    return set()


def apply_damage_packet(
    *,
    game: Any,
    source: Any,
    target: Any,
    packet: DamagePacket,
    ctx: Optional[dict] = None,
) -> int:
    """Apply a DamagePacket to a target via centralized gating/resistance rules.

    Returns the *actual HP damage dealt* (0 if blocked or transformed).
    Healing transforms (e.g., "electric heals flesh golem") are applied internally
    and return 0.
    """

    ctx = dict(ctx or {})
    amount = int(packet.amount or 0)
    if amount <= 0 or target is None:
        return 0

    dt = str(packet.damage_type or "physical").lower()
    prof = getattr(target, "damage_profile", None) or {}
    immunities = _as_set_list(prof, "immunities")
    resistances = _as_set_list(prof, "resistances")
    vulnerabilities = _as_set_list(prof, "vulnerabilities")

    # Material gating (for physical weapon hits).
    requires_material = prof.get("requires_material")
    requires_materials = prof.get("requires_materials")
    req_mats: set[str] = set()
    if isinstance(requires_material, str) and requires_material.strip():
        req_mats.add(requires_material.strip().lower())
    if isinstance(requires_materials, (list, tuple, set)):
        req_mats |= {str(x).strip().lower() for x in requires_materials if str(x).strip()}

    # ---- Magic-weapon gating (only applies to physical weapon hits) ----
    if dt == "physical":
        requires_magic = bool(prof.get("requires_magic_to_hit", False))
        req_bonus = int(prof.get("requires_enchantment_bonus", 0) or 0)
        if requires_magic and (not bool(packet.magical) and int(packet.enchantment_bonus or 0) <= 0):
            try:
                game.ui.log(f"{target.name} is unharmed by non-magical weapons!")
            except Exception:
                pass
            try:
                game.events.emit("damage_blocked_magic", {"target": target.name, "requires": "magic"})
            except Exception:
                pass
            return 0

        if req_mats:
            tags = {str(t).lower() for t in (packet.source_tags or set())}
            if not (tags & req_mats):
                try:
                    need_txt = "/".join(sorted(req_mats))
                    game.ui.log(f"{target.name} can only be harmed by {need_txt} weapons!")
                except Exception:
                    pass
                try:
                    game.events.emit("damage_blocked_material", {"target": target.name, "requires": sorted(req_mats)})
                except Exception:
                    pass
                return 0
        if req_bonus > 0 and int(packet.enchantment_bonus or 0) < req_bonus:
            try:
                game.ui.log(f"{target.name} can only be harmed by +{req_bonus} or better weapons!")
            except Exception:
                pass
            try:
                game.events.emit(
                    "damage_blocked_enchantment",
                    {"target": target.name, "requires_bonus": int(req_bonus), "weapon_bonus": int(packet.enchantment_bonus or 0)},
                )
            except Exception:
                pass
            return 0

    # ---- Immunity ----
    if dt in immunities:
        try:
            game.ui.log(f"{target.name} is immune to {dt}!")
        except Exception:
            pass
        try:
            game.events.emit("damage_immune", {"target": target.name, "damage_type": dt})
        except Exception:
            pass
        return 0

    # ---- Special interactions (transform) ----
    try:
        interactions = dict(prof.get("special_interactions", {}) or {})
    except Exception:
        interactions = {}
    inter = str(interactions.get(dt, "") or "").lower()
    if inter == "heal":
        try:
            target.hp = min(int(getattr(target, "hp_max", target.hp) or target.hp), int(target.hp) + amount)
            game.ui.log(f"{target.name} is energized by {dt} and heals {amount} HP! (HP {target.hp}/{target.hp_max})")
        except Exception:
            pass
        try:
            game.events.emit("damage_transformed_heal", {"target": target.name, "damage_type": dt, "heal": int(amount)})
        except Exception:
            pass
        return 0

    # Special interaction: split (e.g., Ochre Jelly divided by lightning).
    # This is a combat-only effect: electric damage is negated, and the target
    # divides into two creatures with split HP pools.
    if inter == "split":
        try:
            # Only meaningful in combat; require an active foe list from ctx.
            foes = None
            try:
                cand = (ctx or {}).get("combat_foes")
                if isinstance(cand, list):
                    foes = cand
            except Exception:
                foes = None

            cur_hp = int(getattr(target, "hp", 0) or 0)
            cur_max = int(getattr(target, "hp_max", cur_hp) or cur_hp)
            if cur_hp <= 1 or foes is None:
                game.ui.log(f"{target.name} quivers as it is struck by {dt}, but does not divide.")
                game.events.emit("damage_transformed_split_noop", {"target": target.name, "damage_type": dt})
                return 0

            half_a = max(1, cur_hp // 2)
            half_b = max(1, cur_hp - half_a)
            # Set original to half.
            target.hp = half_a
            try:
                target.hp_max = max(1, cur_max // 2)
            except Exception:
                pass

            # Spawn a clone from content if possible; otherwise fall back to a shallow copy.
            clone = None
            mid = str(getattr(target, "monster_id", "") or "").strip()
            if mid:
                try:
                    md = game.content.get_monster(mid)
                    if md:
                        clone = game.encounters._mk_monster(md)
                except Exception:
                    clone = None
            if clone is None:
                try:
                    from copy import deepcopy
                    clone = deepcopy(target)
                except Exception:
                    clone = None

            if clone is not None:
                clone.hp = half_b
                try:
                    clone.hp_max = max(1, int(getattr(clone, "hp_max", half_b) or half_b))
                except Exception:
                    pass
                # Ensure a stable monster_id if present.
                try:
                    if mid and not str(getattr(clone, "monster_id", "") or "").strip():
                        setattr(clone, "monster_id", mid)
                except Exception:
                    pass
                foes.append(clone)
                game.ui.log(f"{target.name} is divided by {dt}! It splits into two creatures!")
                game.events.emit(
                    "damage_transformed_split",
                    {"target": target.name, "damage_type": dt, "hp_a": int(half_a), "hp_b": int(half_b)},
                )
            else:
                game.ui.log(f"{target.name} is divided by {dt}, but no split creature could be created.")
                game.events.emit("damage_transformed_split_failed", {"target": target.name, "damage_type": dt})
        except Exception:
            return 0
        return 0

    # ---- Resistance / vulnerability ----
    final = amount
    if dt in resistances:
        final = max(1, final // 2)
        try:
            game.ui.log(f"{target.name} resists {dt}! (half damage)")
        except Exception:
            pass
        try:
            game.events.emit("damage_resisted", {"target": target.name, "damage_type": dt, "from": int(amount), "to": int(final)})
        except Exception:
            pass
    if dt in vulnerabilities:
        final = final * 2
        try:
            game.ui.log(f"{target.name} is vulnerable to {dt}! (double damage)")
        except Exception:
            pass
        try:
            game.events.emit("damage_vulnerable", {"target": target.name, "damage_type": dt, "from": int(amount), "to": int(final)})
        except Exception:
            pass

    # Apply damage.
    try:
        target.hp = max(-1, int(target.hp) - int(final))
    except Exception:
        return 0

    try:
        game.events.emit(
            "damage_applied",
            {"target": target.name, "damage_type": dt, "amount": int(final), "hp": int(target.hp), "hp_max": int(getattr(target, "hp_max", 0) or 0)},
        )
    except Exception:
        pass

    # Notify effects layer (e.g., regen suppression).
    try:
        getattr(game, "_notify_damage")(target, int(final), damage_types={dt})
    except Exception:
        pass

    # P3.6: centralized death notification for cleanup of control effects
    try:
        if int(getattr(target, "hp", 0) or 0) <= 0 and hasattr(game, "_notify_death"):
            game._notify_death(target, ctx=ctx)
    except Exception:
        pass

    return int(final)

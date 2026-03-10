"""Monster AI behavior profiles (P3.5).

This engine intentionally keeps AI lightweight and deterministic.

Profiles are data-driven via data/monster_ai_profiles.json and keyed by monster_id.
If a monster has no profile, a sane default is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AIProfile:
    aggression: str = "normal"  # normal|high|cautious|fearful|pack
    target_priority: str = "random"  # random|lowest_hp|highest_hp|caster|frontline
    use_missile_if_available: bool = True
    flee_hp_pct: float = 0.0  # 0 disables per-foe flee checks
    morale_mod: int = 0
    prefer_soft_targets_if_has_grab: bool = True


VALID_AGGRESSION = {"normal", "high", "cautious", "fearful", "pack"}
VALID_TARGET_PRIORITY = {"random", "lowest_hp", "highest_hp", "caster", "frontline"}


def coerce_profile(d: Any) -> AIProfile:
    if not isinstance(d, dict):
        return AIProfile()
    aggression = str(d.get("aggression", "normal") or "normal").strip().lower()
    if aggression not in VALID_AGGRESSION:
        aggression = "normal"
    target_priority = str(d.get("target_priority", "random") or "random").strip().lower()
    if target_priority not in VALID_TARGET_PRIORITY:
        target_priority = "random"
    use_missile = bool(d.get("use_missile_if_available", True))
    try:
        flee_hp_pct = float(d.get("flee_hp_pct", 0.0) or 0.0)
    except Exception:
        flee_hp_pct = 0.0
    flee_hp_pct = max(0.0, min(1.0, flee_hp_pct))
    try:
        morale_mod = int(d.get("morale_mod", 0) or 0)
    except Exception:
        morale_mod = 0
    prefer_soft = bool(d.get("prefer_soft_targets_if_has_grab", True))
    return AIProfile(
        aggression=aggression,
        target_priority=target_priority,
        use_missile_if_available=use_missile,
        flee_hp_pct=flee_hp_pct,
        morale_mod=morale_mod,
        prefer_soft_targets_if_has_grab=prefer_soft,
    )


def validate_ai_profiles(ai_profiles: Dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(ai_profiles, dict):
        return ["monster_ai_profiles must be a JSON object keyed by monster_id"]

    for mid, prof in ai_profiles.items():
        if not isinstance(mid, str) or not mid.strip():
            problems.append(f"AI profile key must be a non-empty string monster_id, got: {mid!r}")
            continue
        if not isinstance(prof, dict):
            problems.append(f"AI profile for {mid!r} must be an object")
            continue
        ag = str(prof.get("aggression", "normal") or "normal").strip().lower()
        if ag not in VALID_AGGRESSION:
            problems.append(f"AI profile {mid!r} has invalid aggression: {ag!r}")
        tp = str(prof.get("target_priority", "random") or "random").strip().lower()
        if tp not in VALID_TARGET_PRIORITY:
            problems.append(f"AI profile {mid!r} has invalid target_priority: {tp!r}")
        if "flee_hp_pct" in prof:
            try:
                v = float(prof.get("flee_hp_pct") or 0.0)
                if v < 0.0 or v > 1.0:
                    problems.append(f"AI profile {mid!r} flee_hp_pct must be 0..1, got {v!r}")
            except Exception:
                problems.append(f"AI profile {mid!r} flee_hp_pct must be numeric")
    return problems


def party_role(actor: Any) -> str:
    """Coarse role classification for target selection."""
    cls = str(getattr(actor, "cls", "") or "").strip().lower()
    if cls in ("magic-user", "mage", "wizard"):
        return "caster"
    if cls in ("cleric", "priest"):
        return "caster"
    if cls in ("fighter", "warrior"):
        return "frontline"
    return "other"


def sort_key_name(actor: Any) -> str:
    return str(getattr(actor, "name", "") or "").strip().lower()


def choose_target(candidates: List[Any], *, priority: str, rng_choice_fn) -> Any:
    if not candidates:
        return None
    p = priority
    if p == "lowest_hp":
        return sorted(candidates, key=lambda a: (int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
    if p == "highest_hp":
        return sorted(candidates, key=lambda a: (-int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
    if p == "caster":
        casters = [c for c in candidates if party_role(c) == "caster"]
        if casters:
            return sorted(casters, key=lambda a: (int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
        return sorted(candidates, key=lambda a: (int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
    if p == "frontline":
        fl = [c for c in candidates if party_role(c) == "frontline"]
        if fl:
            return sorted(fl, key=lambda a: (int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
        return sorted(candidates, key=lambda a: (int(getattr(a, "hp", 0) or 0), sort_key_name(a)))[0]
    # random (but deterministic via RNG service)
    return rng_choice_fn(candidates)

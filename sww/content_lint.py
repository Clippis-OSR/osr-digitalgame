from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import os
import re

from .data import DATA_DIR, load_json


@dataclass(frozen=True, slots=True)
class LintIssue:
    code: str
    severity: str  # "ERROR" or "WARN"
    message: str
    where: str  # file/key context


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _iter_monsters(monsters: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(monsters, list):
        for m in monsters:
            if isinstance(m, dict):
                yield m


def _parse_magic_resistance_pct(text: str) -> Optional[int]:
    # Matches:
    #   "Magic resistance (75%)"
    #   "Magic resistance 75%"
    #   "magic resistance ( 25 % )"
    m = re.search(r"magic\s+resistance\s*\(?\s*(\d{1,3})\s*%?\s*\)?", text, flags=re.I)
    if not m:
        return None
    try:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    except Exception:
        return None
    return None


def _mentions_magic_or_silver_only(text: str) -> Tuple[bool, bool]:
    t = text.lower()
    # crude but effective
    only_hit = "only be hit" in t or "can only be hit" in t or "hit only" in t
    mentions_silver = "silver" in t
    mentions_magic = "magic" in t or "magical" in t
    return (only_hit and mentions_magic), (only_hit and mentions_silver)


def lint_content(data_dir: Path = DATA_DIR, strict: bool = False) -> List[LintIssue]:
    """Lint content for cross-file consistency and mechanics/data alignment.

    In strict mode, WARN issues should be treated as build-breaking by the caller.
    """
    issues: List[LintIssue] = []

    monsters = load_json("monsters.json")
    monster_specials = load_json("monster_specials.json")
    monster_damage = load_json("monster_damage_profiles.json")
    monster_ai = load_json("monster_ai_profiles.json")

    monster_ids: set[str] = set()
    alias_map: Dict[str, str] = {}  # normalized alias/name -> monster_id

    # Collect monsters
    for m in _iter_monsters(monsters):
        mid = str(m.get("monster_id") or "").strip()
        name = str(m.get("name") or "").strip()
        if not mid:
            issues.append(LintIssue("LINT001", "ERROR", "Monster missing monster_id.", "monsters.json"))
            continue
        if mid in monster_ids:
            issues.append(LintIssue("LINT002", "ERROR", f"Duplicate monster_id: {mid}", f"monsters.json:{mid}"))
        monster_ids.add(mid)

        # alias collisions (including names)
        keys = []
        if name:
            keys.append(name)
        for a in (m.get("aliases") or []):
            if isinstance(a, str) and a.strip():
                keys.append(a.strip())
        for k in keys:
            nk = _norm(k)
            if nk in alias_map and alias_map[nk] != mid:
                issues.append(
                    LintIssue(
                        "LINT003",
                        "ERROR",
                        f'Alias/name collision "{k}" between {alias_map[nk]} and {mid}.',
                        f"monsters.json:{mid}",
                    )
                )
            else:
                alias_map[nk] = mid

    # Cross-file keys must exist
    def check_keyed_dict(d: Any, filename: str):
        if not isinstance(d, dict):
            issues.append(LintIssue("LINT010", "ERROR", f"{filename} must be a JSON object keyed by monster_id.", filename))
            return
        for k in d.keys():
            if k not in monster_ids:
                issues.append(LintIssue("LINT011", "ERROR", f"{filename} references unknown monster_id: {k}", f"{filename}:{k}"))

    check_keyed_dict(monster_specials, "monster_specials.json")
    check_keyed_dict(monster_damage, "monster_damage_profiles.json")
    check_keyed_dict(monster_ai, "monster_ai_profiles.json")

    # AI coverage
    if isinstance(monster_ai, dict):
        for mid in sorted(monster_ids):
            if mid not in monster_ai:
                issues.append(
                    LintIssue(
                        "LINT020",
                        "WARN" if not strict else "ERROR",
                        f"Missing AI profile for monster_id: {mid}",
                        f"monster_ai_profiles.json:{mid}",
                    )
                )

    # Tag sanity (lightweight)
    # Start with a small canonical set + allow anything seen in file to avoid false positives.
    seen_tags: set[str] = set()
    for m in _iter_monsters(monsters):
        for t in (m.get("tags") or []):
            if isinstance(t, str):
                seen_tags.add(t.strip())
    canonical = {
        "undead",
        "construct",
        "animal",
        "humanoid",
        "demon",
        "devil",
        "giant",
        "ooze",
        "dragon",
    } | {t for t in seen_tags if t}

    for m in _iter_monsters(monsters):
        mid = str(m.get("monster_id") or "").strip()
        tags = m.get("tags") or []
        if tags and not isinstance(tags, list):
            issues.append(LintIssue("LINT030", "ERROR", "tags must be a list of strings.", f"monsters.json:{mid}"))
            continue
        for t in tags:
            if not isinstance(t, str):
                issues.append(LintIssue("LINT031", "ERROR", "tag must be a string.", f"monsters.json:{mid}"))
                continue
            if t.strip() and t.strip() not in canonical:
                issues.append(
                    LintIssue(
                        "LINT032",
                        "WARN" if not strict else "ERROR",
                        f"Unknown monster tag '{t.strip()}'.",
                        f"monsters.json:{mid}",
                    )
                )

    # Mechanics implied by "special" should match structured profiles
    # MR% in monster special text -> damage profile MR%
    if isinstance(monster_damage, dict):
        for m in _iter_monsters(monsters):
            mid = str(m.get("monster_id") or "").strip()
            special_text = " ".join(
                [
                    str(m.get("special") or ""),
                    str(m.get("special_text") or ""),
                ]
            ).strip()
            if not special_text:
                continue

            mr = _parse_magic_resistance_pct(special_text)
            if mr is not None:
                dp = monster_damage.get(mid, {}) if isinstance(monster_damage.get(mid, {}), dict) else {}
                dp_mr = dp.get("magic_resistance_pct", None)
                if dp_mr is None:
                    issues.append(
                        LintIssue(
                            "LINT040",
                            "WARN" if not strict else "ERROR",
                            f"Special text mentions Magic resistance {mr}% but damage profile has no magic_resistance_pct.",
                            f"monsters.json:{mid}",
                        )
                    )
                else:
                    try:
                        dp_mr_i = int(dp_mr)
                    except Exception:
                        dp_mr_i = -1
                    if dp_mr_i != mr:
                        issues.append(
                            LintIssue(
                                "LINT041",
                                "WARN" if not strict else "ERROR",
                                f"Magic resistance mismatch: special says {mr}% but profile says {dp_mr}.",
                                f"monster_damage_profiles.json:{mid}",
                            )
                        )

            # "only hit by magic/silver" gating
            needs_magic, needs_silver = _mentions_magic_or_silver_only(special_text)
            if needs_magic or needs_silver:
                dp = monster_damage.get(mid, {}) if isinstance(monster_damage.get(mid, {}), dict) else {}
                req_magic = bool(dp.get("requires_magic_to_hit", False))
                req_mats = dp.get("requires_materials") or []
                if isinstance(req_mats, str):
                    req_mats = [req_mats]
                req_mats_norm = {str(x).strip().lower() for x in req_mats if str(x).strip()}

                if needs_magic and not req_magic:
                    issues.append(
                        LintIssue(
                            "LINT050",
                            "WARN" if not strict else "ERROR",
                            "Special text implies requires_magic_to_hit but damage profile does not set it.",
                            f"monster_damage_profiles.json:{mid}",
                        )
                    )
                if needs_silver and ("silver" not in req_mats_norm):
                    issues.append(
                        LintIssue(
                            "LINT051",
                            "WARN" if not strict else "ERROR",
                            "Special text implies silver requirement but damage profile requires_materials does not include 'silver'.",
                            f"monster_damage_profiles.json:{mid}",
                        )
                    )

    # Attack string sanity (very lightweight)
    dice_pat = re.compile(r"\b\d+d\d+\b|\b1\s*hp\b", flags=re.I)
    for m in _iter_monsters(monsters):
        mid = str(m.get("monster_id") or "").strip()
        attacks = str(m.get("attacks") or "")
        if attacks and not dice_pat.search(attacks):
            issues.append(
                LintIssue(
                    "LINT060",
                    "WARN" if not strict else "ERROR",
                    "Attacks string does not contain a recognizable dice expression (NdM).",
                    f"monsters.json:{mid}",
                )
            )
        if attacks.strip().endswith(","):
            issues.append(
                LintIssue(
                    "LINT061",
                    "WARN" if not strict else "ERROR",
                    "Attacks string ends with a trailing comma.",
                    f"monsters.json:{mid}",
                )
            )

    return issues

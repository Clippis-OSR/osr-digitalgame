"""Monster library audit (P3.0).

Deterministic, diff-friendly report generator used to keep the content
library consistent as the monster dataset grows.

Run one of:
  python main.py --monster-audit
  python main.py --monster-audit --monster-audit-json monster_audit_report.json
  python -m sww.monster_audit --json monster_audit_report.json
  python scripts/monster_audit.py --json monster_audit_report.json

The audit is intentionally conservative: it reports facts (coverage,
key usage, unresolved references) plus a small set of heuristic "may need"
suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple, Optional
import argparse
import json
import os
import sys

from .data import load_json
from .content_lint import lint_content
from .spell_tags import KNOWN_SPELL_TAGS, normalize_tags


@dataclass(frozen=True)
class AuditReport:
    ok: bool
    text: str
    data: Dict[str, Any]


def _sorted_keys(d: Dict[str, Any] | None) -> List[str]:
    return sorted((d or {}).keys(), key=lambda s: s.lower())


def _monster_names(monsters: Any) -> List[str]:
    """Extract monster names from monsters.json.

    monsters.json is expected to be a list[dict] with a string "name" field.
    """
    out: List[str] = []
    if isinstance(monsters, list):
        for m in monsters:
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                out.append(m["name"].strip())
    return sorted([n for n in out if n], key=lambda s: s.lower())


def _set_lower(xs: Iterable[str]) -> set[str]:
    return {str(x).strip().lower() for x in xs if str(x).strip()}


def _heuristic_candidates(monster_names: List[str]) -> Tuple[List[str], List[str]]:
    """Return (likely_needs_profile, likely_needs_specials).

    Heuristics are name-based only; they are intentionally light-touch.
    """
    needs_profile: List[str] = []
    needs_specials: List[str] = []

    # Profile: gating/resistance/MR are common for these families.
    profile_markers = [
        "demon",
        "devil",
        "daemon",
        "golem",
        "lycan",
        "were",
        "wraith",
        "spectre",
        "ghost",
        "vampire",
        "mummy",
        "elemental",
        "djinn",
        "efreet",
        "djinni",
        "dragon",
        "gargoyle",
        "lich",
        "skeleton",
        "zombie",
        "ooze",
        "slime",
        "jelly",
    ]

    # Specials: on-hit or aura effects are common for these.
    specials_markers = [
        "spider",
        "scorpion",
        "snake",
        "wyvern",
        "ghoul",
        "crawler",
        "carrion",
        "basilisk",
        "cockatrice",
        "medusa",
        "dragon",
        "vampire",
        "mummy",
        "wight",
        "wraith",
        "spectre",
        "lich",
        "troll",
        "hydra",
    ]

    for n in monster_names:
        nl = n.lower()
        if any(k in nl for k in profile_markers):
            needs_profile.append(n)
        if any(k in nl for k in specials_markers):
            needs_specials.append(n)

    # De-dupe while preserving determinism.
    def _uniq_sorted(xs: List[str]) -> List[str]:
        seen: set[str] = set()
        out2: List[str] = []
        for x in sorted(xs, key=lambda s: s.lower()):
            xl = x.lower()
            if xl not in seen:
                seen.add(xl)
                out2.append(x)
        return out2

    return _uniq_sorted(needs_profile), _uniq_sorted(needs_specials)


def run_monster_audit(*, strict_content_report: bool = False) -> AuditReport:
    monsters = load_json("monsters.json")
    try:
        monster_specials: Dict[str, Any] = load_json("monster_specials.json")
    except Exception:
        monster_specials = {}
    try:
        monster_profiles: Dict[str, Any] = load_json("monster_damage_profiles.json")
    except Exception:
        monster_profiles = {}

    # P3.5: Optional monster AI profiles
    try:
        monster_ai_profiles: Dict[str, Any] = load_json("monster_ai_profiles.json")
    except Exception:
        monster_ai_profiles = {}

    # Spells tag coverage (P3.1.11): used by spell-immunity-by-tag.
    try:
        spells: Any = load_json("spells.json")
    except Exception:
        spells = []

    # Monsters are canonicalized by stable monster_id (P3.1.15+).
    names = _monster_names(monsters)
    monster_ids: List[str] = []
    id_to_name: Dict[str, str] = {}
    ident_to_id_l: Dict[str, str] = {}
    if isinstance(monsters, list):
        for m in monsters:
            if not isinstance(m, dict):
                continue
            mid = m.get("monster_id")
            name = m.get("name")
            if isinstance(mid, str) and mid.strip():
                mid = mid.strip()
                monster_ids.append(mid)
                if isinstance(name, str) and name.strip():
                    id_to_name[mid] = name.strip()
                ident_to_id_l[mid.lower()] = mid
            if isinstance(name, str) and name.strip() and isinstance(mid, str) and mid.strip():
                ident_to_id_l[name.strip().lower()] = mid.strip()
            for a in (m.get("aliases") or []):
                if isinstance(a, str) and a.strip() and isinstance(mid, str) and mid.strip():
                    ident_to_id_l[a.strip().lower()] = mid.strip()

    # Helper to get structured effect kinds by monster_id.
    def _effect_kinds_for(monster_id: str) -> set[str]:
        effs = (monster_specials or {}).get(monster_id)
        kinds: set[str] = set()
        if isinstance(effs, list):
            for e in effs:
                if isinstance(e, dict):
                    k = e.get("kind")
                    if isinstance(k, str) and k.strip():
                        kinds.add(k.strip().lower())
        return kinds

    def _resolve_key_to_id(k: str) -> str | None:
        if not isinstance(k, str):
            return None
        return ident_to_id_l.get(k.strip().lower())

    specials_keys = _sorted_keys(monster_specials)
    profiles_keys = _sorted_keys(monster_profiles)
    ai_keys = _sorted_keys(monster_ai_profiles)

    resolved_special_ids: set[str] = set()
    resolved_profile_ids: set[str] = set()
    resolved_ai_ids: set[str] = set()
    dangling_specials: List[str] = []
    dangling_profiles: List[str] = []
    dangling_ai: List[str] = []
    for k in specials_keys:
        mid = _resolve_key_to_id(k)
        if mid:
            resolved_special_ids.add(mid)
        else:
            dangling_specials.append(k)
    for k in profiles_keys:
        mid = _resolve_key_to_id(k)
        if mid:
            resolved_profile_ids.add(mid)
        else:
            dangling_profiles.append(k)
    for k in ai_keys:
        mid = _resolve_key_to_id(k)
        if mid:
            resolved_ai_ids.add(mid)
        else:
            dangling_ai.append(k)

    # Coverage (reported by monster display name).
    monsters_with_specials = [id_to_name.get(mid, mid) for mid in monster_ids if mid in resolved_special_ids]
    monsters_with_profiles = [id_to_name.get(mid, mid) for mid in monster_ids if mid in resolved_profile_ids]

    # Strict-content gap report (P3.2.1): detect monsters that *mention* mechanics
    # in free-text but lack structured specials/profiles that would make those mechanics
    # real when STRICT_CONTENT is enabled.
    strict_missing_structured: Dict[str, List[str]] = {}
    strict_unsupported_mechanics: Dict[str, List[str]] = {}
    if strict_content_report and isinstance(monsters, list):
        # Map text markers -> required structured special kind.
        special_markers: List[Tuple[str, str]] = [
            ("poison", "poison"),
            ("paraly", "paralysis"),
            ("petrif", "petrification"),
            ("turn to stone", "petrification"),
            ("gaze", "gaze_petrification"),
            ("regener", "regeneration"),
            ("energy drain", "energy_drain"),
            ("level drain", "energy_drain"),
            ("fear", "fear_aura"),
            ("panic", "fear_aura"),
            ("charm", "charm"),
            ("disease", "disease"),
            ("rot", "disease"),
            ("grab", "grapple"),
            ("grapple", "grapple"),
            ("constrict", "grapple"),
            ("pinion", "grapple"),
            ("engulf", "engulf"),
            ("swallow", "swallow"),
            ("swallows whole", "swallow"),
        ]
        # Mechanics that are *not* yet modeled via structured specials (future framework work).
        unsupported_markers: List[Tuple[str, str]] = [
            # P3.3: grapple/engulf/swallow are now modeled via structured specials.
            # Leave this list for future mechanics not yet implemented (e.g., breath weapon cones, trample, etc.).
        ]

        for m in monsters:
            if not isinstance(m, dict):
                continue
            mid = m.get("monster_id")
            name = m.get("name")
            if not (isinstance(mid, str) and mid.strip() and isinstance(name, str) and name.strip()):
                continue
            st = m.get("special_text")
            if not isinstance(st, str) or not st.strip():
                continue
            st_l = st.lower()

            kinds = _effect_kinds_for(mid.strip())
            missing: List[str] = []
            for marker, required_kind in special_markers:
                if marker in st_l and required_kind.lower() not in kinds:
                    missing.append(required_kind)
            if missing:
                strict_missing_structured[name.strip()] = sorted(list({x for x in missing}), key=lambda s: s.lower())

            unsupported: List[str] = []
            for marker, mech in unsupported_markers:
                if marker in st_l:
                    unsupported.append(mech)
            if unsupported:
                strict_unsupported_mechanics[name.strip()] = sorted(list({x for x in unsupported}), key=lambda s: s.lower())

    # Field usage snapshot for profiles.
    used_profile_fields: set[str] = set()
    for v in (monster_profiles or {}).values():
        if isinstance(v, dict):
            used_profile_fields |= set(v.keys())
    known_profile_fields = {
        "immunities",
        "resistances",
        "vulnerabilities",
        "requires_magic_to_hit",
        "requires_enchantment_bonus",
        "requires_material",
        "requires_materials",
        "spell_immunity_all",
        "spell_immunity_exempt_spells",
        "spell_immunity_tags",
        "magic_resistance_pct",
        "magic_resistance_exempt_spells",
        "special_interactions",
    }
    unused_profile_fields = sorted(list(known_profile_fields - used_profile_fields))
    unknown_profile_fields = sorted(list(used_profile_fields - known_profile_fields))
    # Save-tag usage snapshot for specials.
    used_save_tags: set[str] = set()
    for effs in (monster_specials or {}).values():
        if isinstance(effs, list):
            for e in effs:
                if isinstance(e, dict) and isinstance(e.get("save_tags"), list):
                    used_save_tags |= {str(t).strip().lower() for t in (e.get("save_tags") or []) if str(t).strip()}
    known_save_tags = {"poison", "paralysis", "petrification", "spells"}
    unknown_save_tags = sorted(list(used_save_tags - known_save_tags))

    # Heuristic "probably missing" lists.
    likely_profile, likely_specials = _heuristic_candidates(names)
    # Convert heuristic name lists to ids if possible, then test coverage by id.
    likely_missing_profile: List[str] = []
    for n in likely_profile:
        mid = _resolve_key_to_id(n)
        if mid and mid not in resolved_profile_ids:
            likely_missing_profile.append(n)
    likely_missing_specials: List[str] = []
    for n in likely_specials:
        mid = _resolve_key_to_id(n)
        if mid and mid not in resolved_special_ids:
            likely_missing_specials.append(n)

    # Build deterministic report.
    total = len(names)
    specials_n = len(monsters_with_specials)
    profiles_n = len(monsters_with_profiles)
    specials_pct = (specials_n / total * 100.0) if total else 0.0
    profiles_pct = (profiles_n / total * 100.0) if total else 0.0

    lines: List[str] = []
    lines.append("P3.0 Monster Library Audit")
    lines.append("=" * 28)
    lines.append("")
    lines.append(f"Monsters: {total}")
    lines.append(f"With specials: {specials_n} ({specials_pct:.1f}%)")
    lines.append(f"With damage profiles: {profiles_n} ({profiles_pct:.1f}%)")
    lines.append("")

    # Spell tag coverage (informational; does not fail the audit).
    spell_total = 0
    spell_with_tags = 0
    untagged_spell_names: List[str] = []
    unknown_spell_tags: Dict[str, List[str]] = {}
    used_spell_tags: set[str] = set()
    if isinstance(spells, list):
        for s in spells:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            spell_total += 1
            tags = normalize_tags(s.get("tags") if isinstance(s, dict) else None)
            if tags:
                spell_with_tags += 1
                used_spell_tags |= set(tags)
            else:
                # Keep the report readable; skip obviously-corrupted names.
                n = name.strip()
                if len(n) <= 60:
                    untagged_spell_names.append(n)
            bad = sorted([t for t in tags if t not in KNOWN_SPELL_TAGS])
            if bad:
                unknown_spell_tags[name.strip()] = bad
    # Spell name sanity (P3.1.12): detect obvious data corruption in spells.json.
    corrupt_spell_names: List[str] = []
    def _is_sane_spell_name(n: str) -> bool:
        if not n or len(n) > 60:
            return False
        if "\n" in n or "." in n:
            return False
        # Allow lowercase-first names, but reject sentence-like names.
        if len(n.split()) > 8:
            return False
        return True
    if isinstance(spells, list):
        for s in spells:
            if not isinstance(s, dict):
                continue
            n = s.get("name")
            if isinstance(n, str):
                nn = n.strip()
                if nn and not _is_sane_spell_name(nn):
                    corrupt_spell_names.append(nn)
    corrupt_spell_names = sorted(list({n for n in corrupt_spell_names}), key=lambda s: s.lower())
    spell_pct = (spell_with_tags / spell_total * 100.0) if spell_total else 0.0

    # Enforcement threshold (P3.4): in STRICT_CONTENT mode default to 100%.
    strict = str(os.getenv("STRICT_CONTENT", "")).strip().lower() in {"1", "true", "yes", "on"}
    try:
        min_cov = float(os.getenv("SPELL_TAG_MIN_COVERAGE", ""))
    except Exception:
        min_cov = 1.0 if strict else 0.95
    # Clamp for safety
    if min_cov < 0.0:
        min_cov = 0.0
    if min_cov > 1.0:
        min_cov = 1.0

    lines.append("Spell tag coverage")
    lines.append("-" * 18)
    lines.append(f"Spells: {spell_total}")
    lines.append(f"With explicit tags: {spell_with_tags} ({spell_pct:.1f}%)")
    lines.append(f"Minimum required coverage: {min_cov*100.0:.1f}%")
    lines.append(f"Unknown spell tags detected: {len(unknown_spell_tags)}")
    lines.append(f"Corrupt spell names detected: {len(corrupt_spell_names)}")
    if corrupt_spell_names:
        lines.append("Corrupt spell names (sample; max 20):")
        for n in corrupt_spell_names[:20]:
            lines.append(f"  - {n}")
    if untagged_spell_names:
        lines.append("Untagged spells (sample; max 40):")
        for n in sorted(untagged_spell_names, key=lambda s: s.lower())[:40]:
            lines.append(f"  - {n}")
    if unknown_spell_tags:
        lines.append("Spells with unknown tags (sample; max 40):")
        for n in sorted(list(unknown_spell_tags.keys()), key=lambda s: s.lower())[:40]:
            bad = ", ".join(unknown_spell_tags[n])
            lines.append(f"  - {n}: [{bad}]")
    lines.append("")

    # Spell immunity-by-tag sanity: tags referenced by monster profiles that are unknown or unused.
    referenced_immunity_tags: set[str] = set()
    for v in (monster_profiles or {}).values():
        if not isinstance(v, dict):
            continue
        for t in (v.get("spell_immunity_tags") or []):
            if isinstance(t, str) and t.strip():
                referenced_immunity_tags.add(t.strip().lower())
    dead_immunity_tags = sorted([t for t in referenced_immunity_tags if t not in used_spell_tags], key=lambda s: s)
    unknown_immunity_tags = sorted([t for t in referenced_immunity_tags if t not in KNOWN_SPELL_TAGS], key=lambda s: s)
    lines.append("Spell immunity tag sanity")
    lines.append("-" * 25)
    lines.append(f"Monster-referenced tags: {len(referenced_immunity_tags)}")
    lines.append(f"Unknown referenced tags: {len(unknown_immunity_tags)}")
    lines.append(f"Referenced-but-unused tags: {len(dead_immunity_tags)}")
    if unknown_immunity_tags:
        lines.append("Unknown tags referenced by monster profiles:")
        lines.extend([f"  - {t}" for t in unknown_immunity_tags])
    if dead_immunity_tags:
        lines.append("Referenced tags not used by any spell:")
        lines.extend([f"  - {t}" for t in dead_immunity_tags])
    lines.append("")

    lines.append("Referential integrity")
    lines.append("-" * 22)
    if not dangling_specials and not dangling_profiles and not dangling_ai:
        lines.append("OK: no dangling specials, profiles, or AI profiles.")
    else:
        if dangling_specials:
            lines.append("Dangling specials keys (no matching monster name):")
            lines.extend([f"  - {k}" for k in dangling_specials])
        if dangling_profiles:
            lines.append("Dangling profile keys (no matching monster name):")
            lines.extend([f"  - {k}" for k in dangling_profiles])
        if dangling_ai:
            lines.append("Dangling AI profile keys (no matching monster name):")
            lines.extend([f"  - {k}" for k in dangling_ai])
    lines.append("")

    lines.append("Damage profile schema coverage")
    lines.append("-" * 29)
    lines.append("Used profile fields:")
    lines.extend([f"  - {k}" for k in sorted(used_profile_fields)])
    if unused_profile_fields:
        lines.append("Unused profile fields (available but not used yet):")
        lines.extend([f"  - {k}" for k in unused_profile_fields])
    if unknown_profile_fields:
        lines.append("Unknown profile fields (typo or schema drift):")
        lines.extend([f"  - {k}" for k in unknown_profile_fields])
    lines.append("")


    lines.append("Specials save-tag coverage")
    lines.append("-" * 25)
    if unknown_save_tags:
        lines.append("Unknown save_tags found (typo or unsupported category):")
        lines.extend([f"  - {t}" for t in unknown_save_tags])
    else:
        lines.append("OK: no unknown save_tags.")
    lines.append("")
    lines.append("Monsters with specials")
    lines.append("-" * 21)
    if monsters_with_specials:
        lines.extend([f"  - {n}" for n in monsters_with_specials])
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Monsters with damage profiles")
    lines.append("-" * 27)
    if monsters_with_profiles:
        lines.extend([f"  - {n}" for n in monsters_with_profiles])
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Monsters with AI profiles")
    lines.append("-" * 24)
    ai_names = sorted([id_to_name.get(mid, mid) for mid in resolved_ai_ids], key=lambda s: str(s).lower())
    if ai_names:
        lines.extend([f"  - {n}" for n in ai_names])
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Heuristic flags (review list)")
    lines.append("-" * 30)
    lines.append("Likely missing a damage profile:")
    if likely_missing_profile:
        lines.extend([f"  - {n}" for n in likely_missing_profile])
    else:
        lines.append("  (none)")
    lines.append("Likely missing specials (effects / auras / on-hit riders):")
    if likely_missing_specials:
        lines.extend([f"  - {n}" for n in likely_missing_specials])
    else:
        lines.append("  (none)")
    lines.append("")

    if strict_content_report:
        lines.append("STRICT_CONTENT gap report")
        lines.append("-" * 25)
        if not strict_missing_structured and not strict_unsupported_mechanics:
            lines.append("OK: no obvious missing structured mechanics detected from special_text.")
        else:
            if strict_missing_structured:
                lines.append("Mentions mechanics in special_text but lacks structured specials:")
                for mn in sorted(strict_missing_structured.keys(), key=lambda s: s.lower()):
                    miss = ", ".join(strict_missing_structured[mn])
                    lines.append(f"  - {mn}: missing specials [{miss}]")
            if strict_unsupported_mechanics:
                lines.append("Mentions unsupported mechanics (needs future framework work):")
                for mn in sorted(strict_unsupported_mechanics.keys(), key=lambda s: s.lower()):
                    miss = ", ".join(strict_unsupported_mechanics[mn])
                    lines.append(f"  - {mn}: mentions [{miss}]")
        lines.append("")

    tags_ok = (spell_total == 0) or (
        (spell_with_tags / spell_total) >= min_cov and not unknown_spell_tags
    )
    ok = (not dangling_specials) and (not dangling_profiles) and (not dangling_ai) and (not unknown_profile_fields) and tags_ok
    lines.append("Audit status: OK" if ok else "Audit status: ISSUES FOUND")

    data: Dict[str, Any] = {
        "schema_version": "P3.5.0",
        "ok": ok,
        "counts": {
            "monsters_total": total,
            "monsters_with_specials": specials_n,
            "monsters_with_profiles": profiles_n,
            "monsters_with_ai_profiles": len(resolved_ai_ids),
            "specials_pct": round(specials_pct, 1),
            "profiles_pct": round(profiles_pct, 1),
        },
        "referential_integrity": {
            "dangling_specials_keys": dangling_specials,
            "dangling_profile_keys": dangling_profiles,
            "dangling_ai_profile_keys": dangling_ai,
        },
        "profile_schema": {
            "known_fields": sorted(list(known_profile_fields)),
            "used_fields": sorted(list(used_profile_fields)),
            "unused_fields": unused_profile_fields,
            "unknown_fields": unknown_profile_fields,
        },
        "coverage": {
            "monsters_with_specials": monsters_with_specials,
            "monsters_with_profiles": monsters_with_profiles,
            "monsters_with_ai_profiles": sorted([id_to_name.get(mid, mid) for mid in resolved_ai_ids], key=lambda s: str(s).lower()),
        },
        "heuristics": {
            "likely_missing_profile": likely_missing_profile,
            "likely_missing_specials": likely_missing_specials,
        },
        "spells": {
            "spells_total": spell_total,
            "spells_with_tags": spell_with_tags,
            "tags_pct": round(spell_pct, 1),
            "min_required_pct": round(min_cov * 100.0, 1),
            "untagged_spell_names_sample": sorted(untagged_spell_names, key=lambda s: s.lower())[:200],
            "unknown_spell_tags_count": len(unknown_spell_tags),
            "unknown_spell_tags_sample": {
                k: unknown_spell_tags[k]
                for k in sorted(list(unknown_spell_tags.keys()), key=lambda s: s.lower())[:200]
            },
            "corrupt_spell_names_count": len(corrupt_spell_names),
            "corrupt_spell_names_sample": corrupt_spell_names[:200],
            "used_spell_tags": sorted(list(used_spell_tags)),
            "monster_referenced_immunity_tags": sorted(list(referenced_immunity_tags)),
            "unknown_monster_referenced_immunity_tags": unknown_immunity_tags,
            "dead_monster_referenced_immunity_tags": dead_immunity_tags,
        },
        "strict_content_gaps": {
            "enabled": bool(strict_content_report),
            "missing_structured_specials": strict_missing_structured if strict_content_report else {},
            "mentions_unsupported_mechanics": strict_unsupported_mechanics if strict_content_report else {},
        },
    }

    return AuditReport(ok=ok, text="\n".join(lines), data=data)


def write_json_report(report: AuditReport, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.data, f, indent=2, sort_keys=True, ensure_ascii=False)


def cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="monster_audit", add_help=True)
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="Write machine-readable audit output to this JSON file path.",
    )
    parser.add_argument(
        "--strict-content-report",
        dest="strict_content_report",
        action="store_true",
        help="Include STRICT_CONTENT gap report: detect mechanics mentioned in special_text but missing structured data.",
    )
    args = parser.parse_args(argv)

    # In STRICT_CONTENT builds (or when requesting strict report), run semantic content lint.
    strict_env = (os.environ.get('STRICT_CONTENT','').strip().lower() == 'true')
    if strict_env or bool(args.strict_content_report):
        issues = lint_content(strict=True)
        if issues:
            print('')
            print('CONTENT LINT (strict)')
            for it in sorted(issues, key=lambda x: (0 if x.severity=='ERROR' else 1, x.code, x.where)):
                print(f"[{it.severity}] {it.code} {it.where}: {it.message}")
            # Fail the audit in strict mode.
            return 2

    rep = run_monster_audit(strict_content_report=bool(args.strict_content_report))
    print(rep.text)
    if args.json_path:
        write_json_report(rep, args.json_path)
        print("")
        print(f"Wrote JSON: {args.json_path}")
    return 0 if rep.ok else 2


def main(argv: Optional[List[str]] = None) -> int:
    # Backwards-compatible entrypoint.
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(cli_main())
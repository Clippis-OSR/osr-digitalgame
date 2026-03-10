# p44/sww/spell_lists_compile.py
from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List

STRICT = os.environ.get("STRICT_CONTENT", "false").lower() == "true"

_LEVEL_RE = re.compile(r"(?P<class>[A-Za-z\- ]+),\s*(?P<lvl>\d+)(?:st|nd|rd|th)\s*,?\s*Level", re.I)


def _sha256_json(obj: Any) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _norm(s: str) -> str:
    s = str(s or "").strip().lower()
    # Remove parenthetical qualifiers (e.g. "Hold Person (Cleric)")
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)
    # Normalize punctuation and separators
    s = s.replace("&", "and")
    s = s.replace("’", "'")
    s = s.replace("magic- user", "magic-user")
    s = re.sub(r"[.,;:]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _name_variants(name: str) -> List[str]:
    n = _norm(name)
    out = {n}
    # Strip trailing class qualifiers (seen in data export)
    out.add(re.sub(r"\s+(cleric|druid|magic-user|magic user)$", "", n).strip())
    out.add(n.replace("10-ft", "10-foot").replace("15-ft", "15-foot"))
    return [x for x in out if x]


def _parse_spell_level_field(spell_level: str) -> Dict[str, int]:
    """
    Parse Spell.spell_level strings into {class_name: level_int}.

    Supports formats seen in our spells.json (including some legacy typos):
      - "Cleric, 7th Level"
      - "Druid 1st Level"
      - "Cleric, Magic-User, Druid 1st Level"
      - "Cleric; Magic-User, 1st Level"   (treats pending class segment as same level)
      - "Cleric, 7th Level; Magic-User, 5th Level"
      - tolerates "2nd, Level" (extra comma)
    """
    out: Dict[str, int] = {}
    raw = str(spell_level or "").strip()
    if not raw:
        return out

    parts = [p.strip() for p in raw.split(";") if p.strip()]
    pending: List[str] = []

    def canon_class(c: str) -> str:
        c = _norm(c)
        c = c.replace("magic user", "magic-user").replace("magicuser", "magic-user")
        return c

    def flush_pending(lvl: int) -> None:
        nonlocal pending
        for pc in pending:
            out[canon_class(pc)] = lvl
        pending = []

    for part in parts:
        # pending class segments without level
        if re.match(r"^[A-Za-z\-\s,]+$", part) and "level" not in part.lower():
            for c in [x.strip() for x in part.split(",") if x.strip()]:
                pending.append(c)
            continue

        # Standard "Class, 7th Level"
        m = _LEVEL_RE.search(part)
        if m:
            lvl = int(m.group("lvl"))
            flush_pending(lvl)
            out[canon_class(m.group("class"))] = lvl
            continue

        # Multi-class single-level: "Cleric, Magic-User, Druid 1st Level"
        m2 = re.search(r"^(?P<classes>.+?)\s+(?P<lvl>\d+)(?:st|nd|rd|th)\s*,?\s*Level$", part, flags=re.I)
        if m2:
            lvl = int(m2.group("lvl"))
            flush_pending(lvl)
            classes_part = m2.group("classes")
            for c in [x.strip() for x in classes_part.split(",") if x.strip()]:
                out[canon_class(c)] = lvl
            continue

        # No-comma single class: "Druid 1st Level"
        m3 = re.search(r"^(?P<class>[A-Za-z\- ]+)\s+(?P<lvl>\d+)(?:st|nd|rd|th)\s*,?\s*Level$", part, flags=re.I)
        if m3:
            lvl = int(m3.group("lvl"))
            flush_pending(lvl)
            out[canon_class(m3.group("class"))] = lvl
            continue

    return out


def compile_spell_lists(root: Path) -> None:
    """
    Compile canonical spell lists into deterministic, validated spell_id lists.

    Inputs:
      - data/spells.json
      - data/spell_lists_canonical_v1.json

    Outputs:
      - data/spell_lists_compiled_v1.json
      - data/spell_lists_unresolved_v1.json
    """
    spells_path = root / "data" / "spells.json"
    source_path = root / "data" / "spell_lists_canonical_v1.json"
    compiled_path = root / "data" / "spell_lists_compiled_v1.json"
    unresolved_path = root / "data" / "spell_lists_unresolved_v1.json"

    spells_raw = json.loads(spells_path.read_text(encoding="utf-8"))
    source = json.loads(source_path.read_text(encoding="utf-8"))

    # Index: normalized spell name -> candidate spell records
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for sp in spells_raw:
        for nm in _name_variants(sp.get("name", "")):
            by_name.setdefault(nm, []).append(sp)
        for a in (sp.get("aliases") or []):
            for an in _name_variants(a):
                by_name.setdefault(an, []).append(sp)

    compiled: Dict[str, Any] = {
        "schema_version": "spell_lists_compiled.v1",
        "build": "P4.10.4",
        "generated_from": "data/spell_lists_canonical_v1.json",
        "classes": {},
        "unresolved": []
    }

    classes = source.get("classes", {}) or {}
    for cls_name, levels in classes.items():
        cls_norm = _norm(cls_name).replace("magic user", "magic-user")
        cls_out: Dict[str, List[Dict[str, str]]] = {}

        if not isinstance(levels, dict):
            continue

        for lvl_key, spell_names in levels.items():
            lvl_int = int(str(lvl_key))
            if not isinstance(spell_names, list):
                continue

            out_list: List[Dict[str, str]] = []
            for disp_name in spell_names:
                # Try multiple stable variants
                keys = _name_variants(disp_name)
                candidates: List[Dict[str, Any]] = []
                for k in keys:
                    candidates = by_name.get(k, [])
                    if candidates:
                        break

                chosen = None
                for sp in candidates:
                    cls_to_lvl = _parse_spell_level_field(sp.get("spell_level", ""))
                    if cls_norm in cls_to_lvl and cls_to_lvl[cls_norm] == lvl_int:
                        chosen = sp
                        break

                if not chosen:
                    compiled["unresolved"].append({
                        "class": cls_name,
                        "level": lvl_int,
                        "name": str(disp_name),
                        "normalized": keys[0] if keys else _norm(disp_name),
                        "reason": "not_found_or_level_mismatch"
                    })
                    continue

                out_list.append({
                    "spell_id": str(chosen.get("spell_id")),
                    "name": str(chosen.get("name"))
                })

            cls_out[str(lvl_int)] = out_list

        compiled["classes"][cls_name] = cls_out

    compiled["content_hash"] = _sha256_json({
        "schema_version": compiled["schema_version"],
        "build": compiled["build"],
        "generated_from": compiled["generated_from"],
        "classes": compiled["classes"]
    })

    compiled_path.write_text(json.dumps(compiled, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    unresolved_path.write_text(json.dumps({
        "schema_version": "spell_lists_unresolved.v1",
        "build": "P4.10.4",
        "unresolved": compiled["unresolved"]
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote: {compiled_path}")
    print(f"Wrote: {unresolved_path}")
    print(f"Unresolved: {len(compiled['unresolved'])}")

    if STRICT and compiled["unresolved"]:
        raise SystemExit("STRICT_CONTENT enabled and unresolved spell list entries remain.")

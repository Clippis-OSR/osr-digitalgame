# p44/sww/dungeon_gen/cl_compile.py

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple

STRICT = os.environ.get("STRICT_CONTENT", "false").lower() == "true"


# ==============================
# Normalization Utilities
# ==============================

def _strip_parenthetical(name: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", name).strip()

def _safe_stub_id(name: str) -> str:
    base = name.lower()
    base = re.sub(r"[(),]", "", base)
    base = re.sub(r"[^a-z0-9]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return f"stub_{base}"

def _latin_plural(name: str) -> str:
    mapping = {
        "hydrae": "hydra",
        "medusae": "medusa",
        "larvae": "larva",
        "fungi": "fungus",
        "cacti": "cactus",
        "succubi": "succubus",
        "incubi": "incubus",
        "djinni": "djinn",
    }
    return mapping.get(name, name)


def _normalize(name: str) -> str:
    n = name.lower().strip()
    n = _strip_parenthetical(n)
    n = re.sub(r"[^a-z0-9,\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _variants(name: str) -> List[str]:
    """
    Generate intelligent name variants for matching.
    """
    base = _normalize(name)

    variants = {base}

    # Remove comma inversion
    if "," in base:
        parts = [p.strip() for p in base.split(",")]
        if len(parts) == 2:
            variants.add(f"{parts[1]} {parts[0]}")

    # Strip plural s
    if base.endswith("s"):
        variants.add(base[:-1])

    # men -> man
    if base.endswith("men"):
        variants.add(base[:-3] + "man")

    # Latin plural
    latin = _latin_plural(base)
    variants.add(latin)

    # Apply plural strip to latin result
    if latin.endswith("s"):
        variants.add(latin[:-1])

    return list(variants)


# ==============================
# Compiler Core
# ==============================

def compile_cl_lists(root: Path) -> None:
    data_dir = root / "data" / "dungeon_gen"
    monsters_path = root / "data" / "monsters.json"
    source_path = data_dir / "dungeon_monster_lists.json"

    compiled_path = data_dir / "dungeon_monster_lists_compiled.json"
    unresolved_path = data_dir / "unresolved_cl_names.json"
    stub_path = data_dir / "auto_monster_stubs.json"

    monsters = json.loads(monsters_path.read_text(encoding="utf-8"))
    source = json.loads(source_path.read_text(encoding="utf-8"))

    # Build lookup index
    monster_index: Dict[str, str] = {}
    for m in monsters:
        mid = m["monster_id"]
        names = [m["name"]] + m.get("aliases", [])
        for n in names:
            for v in _variants(n):
                monster_index[v] = mid

    unresolved: List[Dict[str, Any]] = []
    compiled = {
        "schema_version": "P4.9.2.5",
        "tables": {}
    }

    stubs: List[Dict[str, Any]] = []

    for cl, table in source.get("tables", {}).items():
        compiled["tables"][cl] = {
            "die": table["die"],
            "entries": []
        }

        for entry in table["entries"]:
            new_entry = dict(entry)

            if "monster_id" in entry:
                compiled["tables"][cl]["entries"].append(new_entry)
                continue

            name = entry.get("name")
            if not name:
                compiled["tables"][cl]["entries"].append(new_entry)
                continue

            resolved_id = None
            for v in _variants(name):
                if v in monster_index:
                    resolved_id = monster_index[v]
                    break

            if resolved_id:
                new_entry.pop("name", None)
                new_entry["monster_id"] = resolved_id
                compiled["tables"][cl]["entries"].append(new_entry)
            else:
                unresolved.append({
                    "cl": cl,
                    "context": "entry",
                    "name": name,
                    "range": [entry.get("lo"), entry.get("hi")]
                })

                # Create stub if not STRICT
                stub_id = _safe_stub_id(name)
                stubs.append({
                    "monster_id": stub_id,
                    "name": name,
                    "challenge_level": cl,
                    "stub": True
                })

                new_entry.pop("name", None)
                new_entry["monster_id"] = stub_id
                compiled["tables"][cl]["entries"].append(new_entry)

    # Write compiled
    compiled_path.write_text(
        json.dumps(compiled, indent=2),
        encoding="utf-8"
    )

    unresolved_path.write_text(
        json.dumps({
            "schema_version": "P4.9.2.5",
            "unresolved": unresolved
        }, indent=2),
        encoding="utf-8"
    )

    if stubs:
        stub_path.write_text(
            json.dumps(stubs, indent=2),
            encoding="utf-8"
        )

    print(f"Wrote: {compiled_path}")
    print(f"Wrote: {unresolved_path}")

    if stubs:
        print(f"Wrote: {stub_path}")

    print(f"Unresolved: {len(unresolved)}")

    if STRICT and unresolved:
        raise SystemExit("STRICT_CONTENT enabled and unresolved CL entries remain.")

# Back-compat wrapper used by scripts/validate_dungeon.py
def compile_files(project_root: Path, strict: bool = False):
    """Compile CL lists and return (compiled_path, unresolved_path, unresolved_count)."""
    global STRICT
    # Honor caller strictness for this invocation.
    STRICT = bool(strict)
    compile_cl_lists(project_root)
    data_dir = project_root / "data" / "dungeon_gen"
    compiled_path = data_dir / "dungeon_monster_lists_compiled.json"
    unresolved_path = data_dir / "unresolved_cl_names.json"
    try:
        u = json.loads(unresolved_path.read_text(encoding="utf-8"))
        n = len(u.get("unresolved") or [])
    except Exception:
        n = 0
    return compiled_path, unresolved_path, n

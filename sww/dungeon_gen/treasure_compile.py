# p44/sww/dungeon_gen/treasure_compile.py

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from p44.sww.items import load_magic_items


STRICT = os.environ.get("STRICT_CONTENT", "false").lower() == "true"


_RANGE_RE = re.compile(r"^(\d{1,3})-(\d{1,3})$")
_DICE_RE = re.compile(r"^d(\d+)$")


def _parse_range(s: str) -> Tuple[int, int]:
    m = _RANGE_RE.match(str(s).strip())
    if not m:
        raise ValueError(f"Invalid range string: {s!r} (expected '01-10' etc)")
    lo = int(m.group(1))
    hi = int(m.group(2))

    # Common d100 convention: '00' means 100
    if lo == 0:
        lo = 100
    if hi == 0:
        hi = 100

    if lo < 1 or hi < lo:
        raise ValueError(f"Invalid range bounds: {s!r}")
    return lo, hi


def _parse_dice(s: str) -> Dict[str, int]:
    """Only supports the canonical table dice for compilation: dN (e.g. d100)."""
    m = _DICE_RE.match(str(s).strip().lower())
    if not m:
        raise ValueError(f"Invalid dice string: {s!r} (expected 'd100', 'd20', etc)")
    return {"count": 1, "sides": int(m.group(1))}


def _sha256_json(obj: Any) -> str:
    txt = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(txt.encode("utf-8")).hexdigest()


def _validate_coverage(entries: List[Dict[str, Any]], sides: int) -> Tuple[bool, bool, List[str]]:
    """Return (coverage_ok, overlap_ok, errors)."""
    used = [0] * (sides + 1)  # 1..sides
    overlap_ok = True
    for e in entries:
        lo = int(e["lo"])
        hi = int(e["hi"])
        for v in range(lo, hi + 1):
            if v < 1 or v > sides:
                overlap_ok = False
                continue
            used[v] += 1
            if used[v] > 1:
                overlap_ok = False

    coverage_ok = all(used[i] == 1 for i in range(1, sides + 1))

    errors: List[str] = []
    if not coverage_ok:
        missing = [str(i) for i in range(1, sides + 1) if used[i] == 0]
        if missing:
            errors.append(f"Missing values: {', '.join(missing[:25])}{'…' if len(missing) > 25 else ''}")
    if not overlap_ok:
        overlaps = [str(i) for i in range(1, sides + 1) if used[i] > 1]
        if overlaps:
            errors.append(f"Overlapping values: {', '.join(overlaps[:25])}{'…' if len(overlaps) > 25 else ''}")
    return coverage_ok, overlap_ok, errors


def compile_treasure_tables(root: Path) -> None:
    """Compile raw treasure tables into a normalized, validated, hashed artifact.

    Input:  p44/data/treasure_tables_v1.json
    Output: p44/data/treasure_compiled_v1.json
            p44/data/treasure_unresolved_v1.json
    """
    data_dir = root / "data"
    src_path = data_dir / "treasure_tables_v1.json"
    out_path = data_dir / "treasure_compiled_v1.json"
    unresolved_path = data_dir / "treasure_unresolved_v1.json"

    src = json.loads(src_path.read_text(encoding="utf-8"))

    # Optional: resolve magic list item_ids against our minimal magic item registry.
    # This keeps STRICT_CONTENT meaningful while still allowing template-style items.
    registry = None
    try:
        registry = load_magic_items(root)
    except Exception:
        # Repo may not include item data in some contexts; compilation still works.
        registry = None

    value_tables = src.get("value_tables", {}) or {}
    magic_lists = src.get("magic_lists", []) or []
    tables = src.get("tables", []) or []

    # Index magic lists by id
    magic_by_id: Dict[str, Any] = {}
    for ml in magic_lists:
        lid = ml.get("list_id")
        if not lid:
            raise ValueError("magic_lists entry missing list_id")
        if lid in magic_by_id:
            raise ValueError(f"Duplicate magic list_id: {lid}")
        magic_by_id[lid] = ml

    unresolved: List[Dict[str, Any]] = []

    compiled: Dict[str, Any] = {
        "schema_version": "treasure_compiled.v1",
        "generated_from": str(src_path.relative_to(root)),
        "tables_by_id": {},
        "value_tables_by_id": {},
        "magic_lists_by_id": {},
        "unresolved": unresolved,
    }

    # Compile value tables
    for vt_id, vt in value_tables.items():
        dice_obj = _parse_dice(vt.get("dice", ""))
        sides = dice_obj["sides"]
        bands_raw = vt.get("bands") or []
        bands_compiled: List[Dict[str, Any]] = []
        for i, b in enumerate(bands_raw):
            lo, hi = _parse_range(b.get("range"))
            bands_compiled.append({"lo": lo, "hi": hi, "gp_value": int(b.get("gp_value"))})
        cov_ok, ov_ok, errs = _validate_coverage(bands_compiled, sides)
        if not (cov_ok and ov_ok):
            unresolved.append({
                "kind": "value_table_coverage",
                "ref": vt_id,
                "where": f"value_tables.{vt_id}",
                "errors": errs,
            })
        compiled["value_tables_by_id"][vt_id] = {
            "dice": dice_obj,
            "bands": bands_compiled,
        }

    # Compile magic lists
    for ml_id, ml in magic_by_id.items():
        dice_obj = _parse_dice(ml.get("dice", ""))
        sides = dice_obj["sides"]
        entries_compiled: List[Dict[str, Any]] = []
        for idx, e in enumerate(ml.get("entries") or []):
            lo, hi = _parse_range(e.get("range"))
            item_id = str(e.get("item_id") or "").strip()
            if not item_id:
                unresolved.append({
                    "kind": "magic_item_id",
                    "ref": "<missing>",
                    "where": f"magic_lists.{ml_id}.entries[{idx}]",
                })
                item_id = "<missing>"
            # Resolution is strict when we have an item registry:
            # - concrete item ids must exist
            # - templates must exist in registry.templates
            resolved = True
            ref_kind = None
            if registry is not None:
                if item_id in registry.item_ids:
                    resolved = True
                elif item_id in registry.template_ids:
                    resolved = True
                else:
                    resolved = False
                    ref_kind = "magic_item_id"
            else:
                # Fallback heuristic (legacy): treat *_any tokens as templates.
                is_template = item_id.endswith("_any") or item_id.endswith(":any")
                resolved = not is_template
                if is_template:
                    ref_kind = "magic_item_template"

            entries_compiled.append({"lo": lo, "hi": hi, "item_id": item_id, "resolved": resolved})
            if not resolved:
                unresolved.append({
                    "kind": ref_kind or "magic_item_id",
                    "ref": item_id,
                    "where": f"magic_lists.{ml_id}.entries[{idx}]",
                })
        cov_ok, ov_ok, errs = _validate_coverage(entries_compiled, sides)
        if not (cov_ok and ov_ok):
            unresolved.append({
                "kind": "magic_list_coverage",
                "ref": ml_id,
                "where": f"magic_lists.{ml_id}",
                "errors": errs,
            })
        compiled["magic_lists_by_id"][ml_id] = {"dice": dice_obj, "entries": entries_compiled}

    # Compile treasure tables
    for t in tables:
        table_id = str(t.get("table_id") or "").strip()
        if not table_id:
            raise ValueError("Treasure table missing table_id")
        kind = str(t.get("kind") or "").strip()
        if kind not in ("hoard", "individual"):
            raise ValueError(f"Treasure table {table_id}: invalid kind {kind!r}")
        dice_obj = _parse_dice(t.get("dice", ""))
        sides = dice_obj["sides"]

        entries_compiled: List[Dict[str, Any]] = []

        for ei, e in enumerate(t.get("entries") or []):
            lo, hi = _parse_range(e.get("range"))
            drops = e.get("drops") or []
            drops_compiled: List[Dict[str, Any]] = []

            for di, d in enumerate(drops):
                dtype = d.get("type")
                if dtype == "coins":
                    rolls: Dict[str, Any] = {}
                    for denom in ("cp", "sp", "ep", "gp", "pp"):
                        if denom in d:
                            rolls[denom] = {"expr": str(d[denom])}
                    drops_compiled.append({"type": "coins", "rolls": rolls})

                elif dtype in ("gems", "jewelry"):
                    vt_id = str(d.get("value_table") or "").strip()
                    if not vt_id or vt_id not in compiled["value_tables_by_id"]:
                        unresolved.append({
                            "kind": "value_table_ref",
                            "ref": vt_id or "<missing>",
                            "where": f"tables.{table_id}.entries[{ei}].drops[{di}]",
                        })
                    drops_compiled.append({
                        "type": dtype,
                        "count": {"expr": str(d.get("count"))},
                        "value_table_id": vt_id,
                    })

                elif dtype == "magic":
                    ml_id = str(d.get("list_id") or "").strip()
                    if not ml_id or ml_id not in compiled["magic_lists_by_id"]:
                        unresolved.append({
                            "kind": "magic_list_ref",
                            "ref": ml_id or "<missing>",
                            "where": f"tables.{table_id}.entries[{ei}].drops[{di}]",
                        })
                    picks = d.get("picks")
                    picks_expr = str(picks) if not isinstance(picks, int) else str(int(picks))
                    drops_compiled.append({
                        "type": "magic",
                        "picks": {"expr": picks_expr},
                        "magic_list_id": ml_id,
                        "resolved": {
                            "mode": "lookup_item_ids",
                            "all_item_ids_resolved": False,
                        },
                    })

                else:
                    unresolved.append({
                        "kind": "drop_type",
                        "ref": str(dtype),
                        "where": f"tables.{table_id}.entries[{ei}].drops[{di}]",
                    })

            entries_compiled.append({"lo": lo, "hi": hi, "drops": drops_compiled})

        cov_ok, ov_ok, errs = _validate_coverage(entries_compiled, sides)
        if not (cov_ok and ov_ok):
            unresolved.append({
                "kind": "treasure_table_coverage",
                "ref": table_id,
                "where": f"tables.{table_id}",
                "errors": errs,
            })

        compiled["tables_by_id"][table_id] = {
            "table_id": table_id,
            "kind": kind,
            "dice": dice_obj,
            "entries": entries_compiled,
            "validation": {"coverage_ok": cov_ok, "overlap_ok": ov_ok},
        }

    # Content hash over the compiled payload (excluding content_hash itself)
    compiled_for_hash = dict(compiled)
    compiled_for_hash.pop("content_hash", None)
    compiled["content_hash"] = _sha256_json(compiled_for_hash)

    out_path.write_text(json.dumps(compiled, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    unresolved_path.write_text(
        json.dumps({"schema_version": "treasure_unresolved.v1", "unresolved": unresolved}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {out_path}")
    print(f"Wrote: {unresolved_path}")
    print(f"Unresolved: {len(unresolved)}")

    if STRICT and unresolved:
        raise SystemExit("STRICT_CONTENT enabled and unresolved treasure entries remain.")

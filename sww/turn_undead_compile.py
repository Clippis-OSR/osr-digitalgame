"""Turning the Undead table compiler (P4.10.6).

We keep the simulation deterministic and CI-friendly by compiling an
author-editable JSON input into a normalized, validated, hash-stable output.

Design mirrors the CL compiler / treasure table compiler patterns:
  - raw input (author-editable)
  - compiled output (normalized, strict)
  - validation reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _norm_id(name: str) -> str:
    s = "".join(ch.lower() if ch.isalnum() else "_" for ch in (name or ""))
    s = "_".join([p for p in s.split("_") if p])
    return s or "undead"


def _coerce_cell(v: Any) -> str:
    """Return a normalized cell.

    - "T", "D", "-" allowed
    - integers 2..20 allowed (targets for 2d10)
    """
    if v is None:
        return "-"
    if isinstance(v, int):
        if 2 <= v <= 20:
            return str(v)
        raise ValueError(f"turn_undead cell int out of range: {v}")
    s = str(v).strip()
    if not s:
        return "-"
    if s == "–":
        return "-"
    up = s.upper()
    if up in ("T", "D"):
        return up
    if up == "-":
        return "-"
    try:
        n = int(s)
    except Exception as e:
        raise ValueError(f"turn_undead cell not understood: {v!r}") from e
    if 2 <= n <= 20:
        return str(n)
    raise ValueError(f"turn_undead cell int out of range: {n}")


@dataclass
class TurnUndeadCompileReport:
    ok: bool
    errors: list[str]
    warnings: list[str]
    compiled_count: int


def compile_turn_undead(raw: dict | list) -> tuple[dict, TurnUndeadCompileReport]:
    """Compile turning undead tables.

    Accepts either:
      - legacy list form (data/turn_undead.json)
      - object form: {schema_version, dice, columns, undead:[...]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(raw, list):
        undead_rows = raw
        dice = "2d10"
        columns = 12
    elif isinstance(raw, dict):
        undead_rows = list(raw.get("undead") or [])
        dice = str(raw.get("dice") or "2d10")
        columns = int(raw.get("columns") or 12)
    else:
        return ({}, TurnUndeadCompileReport(False, ["raw is neither list nor dict"], [], 0))

    if dice != "2d10":
        warnings.append(f"dice expected 2d10; got {dice!r} (continuing)")
    if columns != 12:
        warnings.append(f"columns expected 12; got {columns} (continuing)")

    compiled_entries: list[dict] = []
    seen_ids: set[str] = set()
    for i, row in enumerate(undead_rows):
        if not isinstance(row, dict):
            errors.append(f"row {i}: not an object")
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            errors.append(f"row {i}: missing name")
            continue
        undead_id = str(row.get("undead_id") or _norm_id(name)).strip()
        # Some legacy tables legitimately repeat a name at different undead tiers.
        # Keep determinism: auto-suffix with undead_cl (or row index).
        if undead_id in seen_ids:
            try:
                uc = int(row.get("undead_cl") or 0)
            except Exception:
                uc = 0
            suffix = str(uc) if uc > 0 else str(i)
            undead_id = f"{undead_id}_{suffix}"
        if undead_id in seen_ids:
            errors.append(f"row {i}: duplicate undead_id {undead_id!r}")
            continue
        seen_ids.add(undead_id)

        try:
            undead_cl = int(row.get("undead_cl") or 0)
        except Exception:
            undead_cl = 0
        if undead_cl <= 0:
            warnings.append(f"row {i} ({name}): undead_cl missing/invalid")

        results = list(row.get("results") or [])
        if len(results) != 12:
            errors.append(f"row {i} ({name}): results must have 12 entries (got {len(results)})")
            continue
        try:
            results_norm = [_coerce_cell(v) for v in results]
        except Exception as e:
            errors.append(f"row {i} ({name}): {e}")
            continue

        compiled_entries.append(
            {
                "undead_id": undead_id,
                "name": name,
                "undead_cl": undead_cl,
                "results": results_norm,
            }
        )

    compiled = {
        "schema_version": "turn_undead_compiled.v1",
        "dice": "2d10",
        "columns": 12,
        "undead": compiled_entries,
    }

    ok = not errors
    return compiled, TurnUndeadCompileReport(ok=ok, errors=errors, warnings=warnings, compiled_count=len(compiled_entries))

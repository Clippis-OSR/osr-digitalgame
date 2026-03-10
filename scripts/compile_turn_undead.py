"""Compile Turning the Undead table into a normalized deterministic form."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    data_dir = root / "data"
    raw_path = data_dir / "turn_undead.json"  # legacy raw form
    out_path = data_dir / "turn_undead_compiled.json"

    from sww.data import load_json
    from sww.turn_undead_compile import compile_turn_undead

    raw = load_json(str(raw_path.name))
    compiled, report = compile_turn_undead(raw)

    if report.warnings:
        for w in report.warnings:
            print(f"WARN: {w}", file=sys.stderr)
    if not report.ok:
        for e in report.errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2

    out_path.write_text(json.dumps(compiled, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({report.compiled_count} undead rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

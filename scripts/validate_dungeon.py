from __future__ import annotations

import argparse
import os
import sys

# Ensure repo root (p44/) is on sys.path when running as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sww.dungeon_gen.pipeline import run_canonical_blueprint_stage, run_canonical_level_pipeline
from sww.dungeon_gen.blueprint import stable_hash_dict
from sww.dungeon_gen.cl_compile import compile_files


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch validate dungeon generation (P4.9.1 + P4.9.2)")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=60)
    ap.add_argument("--depth", type=int, default=1)
    args = ap.parse_args()

    # CL list compilation check (deterministic, no RNG).
    # If STRICT_CONTENT=true, unresolved entries are fatal.
    from pathlib import Path

    strict = str(os.getenv("STRICT_CONTENT", "")).lower() in ("1", "true", "yes")
    try:
        _, unresolved_path, n = compile_files(
            project_root=Path(os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))),
            strict=strict,
        )
        if n:
            print(f"WARN CL list unresolved={n} (see {unresolved_path})")
    except Exception as e:
        print(f"FAIL CL list compilation: {e}")
        return 2

    failed = 0
    for i in range(args.runs):
        seed = str(1000 + i)
        a1 = run_canonical_blueprint_stage(level_id="level_1", seed=seed, width=args.width, height=args.height, depth=args.depth)
        a2 = run_canonical_blueprint_stage(level_id="level_1", seed=seed, width=args.width, height=args.height, depth=args.depth)
        if a1.report.get("validation", {}).get("passed") is not True:
            failed += 1
            print(f"FAIL seed={seed} validation")
            continue
        if a1.blueprint.sha256() != a2.blueprint.sha256():
            failed += 1
            print(f"FAIL seed={seed} nondeterministic hash")

        # Stocking determinism (P4.9.2)
        l1 = run_canonical_level_pipeline(level_id="level_1", seed=seed, width=args.width, height=args.height, depth=args.depth)
        l2 = run_canonical_level_pipeline(level_id="level_1", seed=seed, width=args.width, height=args.height, depth=args.depth)
        h1 = stable_hash_dict(l1.stocking)
        h2 = stable_hash_dict(l2.stocking)
        if h1 != h2:
            failed += 1
            print(f"FAIL seed={seed} nondeterministic stocking")
    if failed:
        print(f"FAILED {failed}/{args.runs}")
        return 2
    print(f"OK {args.runs}/{args.runs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

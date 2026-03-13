from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure repo root (p44/) is on sys.path when running as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sww.dungeon_gen.pipeline import run_canonical_blueprint_stage, run_canonical_level_pipeline
from sww.dungeon_gen.generate import write_artifacts, write_level_artifacts


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate dungeon artifacts (P4.9.1 blueprint or P4.9.2 level stocking)")
    ap.add_argument("--seed", required=True, help="Base seed (string or int)")
    ap.add_argument("--level-id", default="level_1", help="Level id")
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=60)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--out", default="artifacts", help="Output directory")
    ap.add_argument(
        "--blueprint-only",
        action="store_true",
        help="Only generate the P4.9.1 blueprint + report (skip P4.9.2 stocking).",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    basename = f"{args.level_id}_d{args.depth}_seed_{args.seed}"

    if args.blueprint_only:
        art = run_canonical_blueprint_stage(
            level_id=args.level_id,
            seed=str(args.seed),
            width=args.width,
            height=args.height,
            depth=args.depth,
        )
        bp, rep = write_artifacts(art, out_dir=out_dir, basename=basename)
        print(f"Wrote: {bp}")
        print(f"Wrote: {rep}")
        print(f"Blueprint sha256: {art.blueprint.sha256()}")
        return 0

    art2 = run_canonical_level_pipeline(
        level_id=args.level_id,
        seed=str(args.seed),
        width=args.width,
        height=args.height,
        depth=args.depth,
    )
    bp, st, inst, rep = write_level_artifacts(art2, out_dir=out_dir, basename=basename)
    print(f"Wrote: {bp}")
    print(f"Wrote: {st}")
    print(f"Wrote: {inst}")
    print(f"Wrote: {rep}")
    print(f"Blueprint sha256: {art2.blueprint.sha256()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

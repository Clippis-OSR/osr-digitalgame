"""Validate that save/load continuation matches uninterrupted replay.

Usage:
  python -m scripts.validate_save_load_equivalence tests/fixtures/replays/save_load_replay_equivalence
  python -m scripts.validate_save_load_equivalence --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _fixture_dirs(root: Path) -> list[Path]:
    """Return only dedicated save/load equivalence fixtures.

    The replays directory contains many golden replay fixtures whose purpose is
    strict replay verification, not save/load branch equivalence. We only want
    fixture dirs that explicitly opt in via replay meta.save_load_split_after,
    or the canonical save_load_replay_equivalence fixture directory name.
    """
    import json

    out: list[Path] = []
    if not root.exists() or not root.is_dir():
        return out
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        base_p = p / "base_save.json"
        rep_p = p / "replay.json"
        if not base_p.exists() or not rep_p.exists():
            continue
        include = p.name == "save_load_replay_equivalence"
        if not include:
            try:
                rep = json.loads(rep_p.read_text(encoding="utf-8"))
                meta = rep.get("meta") if isinstance(rep, dict) else {}
                include = isinstance(meta, dict) and meta.get("save_load_split_after") is not None
            except Exception:
                include = False
        if include:
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture_dir", nargs="?", default="")
    ap.add_argument("--root", default="tests/fixtures/equivalence")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--split-after", type=int, default=None)
    args = ap.parse_args(argv)

    from sww.replay_equivalence import run_equivalence_fixture_dir

    if args.all:
        dirs = _fixture_dirs(Path(args.root))
        if not dirs:
            print(f"[equivalence] No fixtures found under {args.root}")
            return 0
        failed = 0
        for fx in dirs:
            res = run_equivalence_fixture_dir(fx, split_after=args.split_after)
            mark = "PASS" if res.ok else "FAIL"
            print(f"[equivalence] {mark} {fx.name}: {res.message}")
            if not res.ok:
                failed += 1
        return 1 if failed else 0

    if not args.fixture_dir:
        print("Usage: python -m scripts.validate_save_load_equivalence <fixture_dir> [--split-after N] or --all")
        return 2

    fx_dir = Path(args.fixture_dir)
    res = run_equivalence_fixture_dir(fx_dir, split_after=args.split_after)
    mark = "PASS" if res.ok else "FAIL"
    print(f"[equivalence] {mark} {fx_dir.name}: {res.message}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

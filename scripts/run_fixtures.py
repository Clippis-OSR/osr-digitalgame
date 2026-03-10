"""Run golden replay fixtures.

Usage:
  python -m scripts.run_fixtures
  python -m scripts.run_fixtures --root tests/fixtures/replays

Exit code:
  0 = all fixtures passed
  1 = any fixture failed
"""
#ONLY = {"spell_disruption_edge"}  # TEMP
from __future__ import annotations

from pathlib import Path
import sys


def _parse_root(argv: list[str]) -> Path | None:
    for i, a in enumerate(argv):
        if a.startswith("--root="):
            p = a.split("=", 1)[1].strip()
            return Path(p) if p else None
        if a == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1])
    return None


def main(argv: list[str]) -> int:
    # Ensure local imports work when run as a module.
    from sww.fixtures_runner import default_fixtures_root, run_all_fixtures

    root = _parse_root(argv) or default_fixtures_root()
    ok, results = run_all_fixtures(root)

    if not results:
        print(f"[fixtures] No fixtures found at: {root}")
        return 0

    failed = [r for r in results if not r.ok]
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"[fixtures] {mark} {r.name}: {r.message}")

    if failed:
        print(f"[fixtures] {len(failed)}/{len(results)} failed")
        return 1
    print(f"[fixtures] {len(results)}/{len(results)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

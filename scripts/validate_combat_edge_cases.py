"""Validate replay-sensitive combat edge cases.

Runs the core combat fixtures that exercise morale, retreat/pursuit,
spell disruption, and mid-round actor removal.
"""
from __future__ import annotations

from pathlib import Path
import sys


EDGE_FIXTURES = (
    "morale_retreat_pursuit",
    "spell_disruption_edge",
    "grapple_escape",
    "swallow_cut_out",
    "cover_take_cover_missile_duel",
)


def main(argv: list[str]) -> int:
    from sww.fixtures_runner import default_fixtures_root, run_all_fixtures

    root = default_fixtures_root()
    ok, results = run_all_fixtures(Path(root), only=EDGE_FIXTURES)
    if not results:
        print("[combat-edge] No fixtures found")
        return 1
    failed = [r for r in results if not r.ok]
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"[combat-edge] {mark} {r.name}: {r.message}")
    if failed:
        print(f"[combat-edge] {len(failed)}/{len(results)} failed")
        return 1
    print(f"[combat-edge] {len(results)}/{len(results)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Compile raw treasure tables into a normalized compiled artifact.

Usage:
  python scripts/compile_treasure_tables.py

Outputs:
  data/treasure_compiled_v1.json
  data/treasure_unresolved_v1.json

If STRICT_CONTENT=true and any unresolved entries remain, exits non-zero.
"""

import sys
from pathlib import Path


def main() -> int:
    # Ensure project root is on path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from sww.dungeon_gen.treasure_compile import compile_treasure_tables

    compile_treasure_tables(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

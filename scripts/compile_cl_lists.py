"""Compile CL monster lists into monster_id tables.

Usage:
  python scripts/compile_cl_lists.py

Outputs:
  data/dungeon_gen/dungeon_monster_lists_compiled.json
  data/dungeon_gen/unresolved_cl_names.json

If STRICT_CONTENT=true and any entries cannot be resolved, exits non-zero.
"""

# p44/scripts/compile_cl_lists.py

import sys
from pathlib import Path


def main() -> int:
    # Ensure project root is on path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from sww.dungeon_gen.cl_compile import compile_cl_lists

    compile_cl_lists(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

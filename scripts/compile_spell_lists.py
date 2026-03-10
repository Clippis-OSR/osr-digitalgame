"""Compile canonical spell lists (P4.10.4).

Usage:
  python scripts/compile_spell_lists.py

Outputs:
  data/spell_lists_compiled_v1.json
  data/spell_lists_unresolved_v1.json

If STRICT_CONTENT=true and any entries cannot be resolved, exits non-zero.
"""

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from sww.spell_lists_compile import compile_spell_lists

    compile_spell_lists(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

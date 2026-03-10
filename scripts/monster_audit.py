"""Convenience wrapper for the monster library audit.

Run:
  python scripts/monster_audit.py
  python scripts/monster_audit.py --json monster_audit_report.json

Equivalent to:
  python -m sww.monster_audit ...
"""

from __future__ import annotations

import sys
from sww.monster_audit import cli_main


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))

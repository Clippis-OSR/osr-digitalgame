"""Replay verifier (P3.6).

Usage:
  python -m scripts.replay_verify path/to/save.json

Loads the save, extracts the embedded replay log, and re-runs commands with the
stored seeds. In STRICT_CONTENT it will also require content hashes to match.
"""

from __future__ import annotations

import json
import sys

from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import apply_game_dict
from sww.replay_runner import run_replay


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python -m scripts.replay_verify [--strict] path/to/save.json")
        return 2

    strict = False
    path = None
    for a in argv[1:]:
        if a.strip() == "--strict":
            strict = True
            continue
        if path is None:
            path = a

    if not path:
        print("Usage: python -m scripts.replay_verify [--strict] path/to/save.json")
        return 2
    data = json.loads(open(path, "r", encoding="utf-8").read())

    ui = HeadlessUI()
    g = Game(ui)
    apply_game_dict(g, data)

    rep = (data.get("replay") or {})
    ok, msg = run_replay(
        g,
        rep,
        verify_rng=bool(strict),
        verify_ai=bool(strict),
        use_recorded_rng=False,
    )
    if not ok:
        print("REPLAY FAILED:", msg)
        return 1
    print("REPLAY OK" + (" (STRICT)" if strict else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Bless (or re-bless) a golden replay fixture.

Usage:
  python -m scripts.bless_fixture tests/fixtures/replays/contract_scout

This writes/overwrites expected.json with stable sha256 hashes for:
  - snapshot_state(game) after strict replay
  - rng_events
  - ai_events

Notes:
  - This does NOT generate base_save.json or replay.json. Create those first.
  - Intended workflow: update engine -> run fixtures -> if change is intentional -> bless.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys


def _stable_hash(obj) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python -m scripts.bless_fixture <fixture_dir>")
        return 2

    fx_dir = Path(argv[0])
    base_p = fx_dir / "base_save.json"
    rep_p = fx_dir / "replay.json"
    if not base_p.exists() or not rep_p.exists():
        print("Missing base_save.json or replay.json")
        return 2

    from sww.game import Game
    from sww.save_load import apply_game_dict
    from sww.replay_runner import run_replay
    from sww.debug_diff import snapshot_state
    from sww.selftest import DummyUI
    from sww.scripted_ui import ScriptedUI

    base = json.loads(base_p.read_text(encoding="utf-8"))
    rep = json.loads(rep_p.read_text(encoding="utf-8"))

    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}

    ui_actions = meta.get("ui_actions") if isinstance(meta.get("ui_actions"), list) else None
    if ui_actions is not None:
        ui = ScriptedUI(actions=ui_actions, auto_default=True, echo=False)
    else:
        ui = DummyUI()
    # respect auto_combat if provided
    try:
        if hasattr(ui, "auto_combat") and ("auto_combat" in meta):
            ui.auto_combat = bool(meta.get("auto_combat"))
    except Exception:
        pass

    g = Game(ui, dice_seed=99999, wilderness_seed=99999)
    g.enable_validation = True
    apply_game_dict(g, base)

    ok, msg = run_replay(g, rep, verify_rng=True, verify_ai=True, use_recorded_rng=True)
    if not ok:
        print(f"Replay failed; cannot bless: {msg}")
        return 1

    snap = snapshot_state(g)
    expected = {
        "snapshot_sha256": _stable_hash(snap),
        "rng_sha256": _stable_hash(rep.get("rng_events") or []),
        "ai_sha256": _stable_hash(rep.get("ai_events") or []),
    }

    outp = fx_dir / "expected.json"
    outp.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Blessed: {fx_dir} -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

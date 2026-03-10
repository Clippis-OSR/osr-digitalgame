"""Audit combat intent/legality rewrites across replay fixtures."""
from __future__ import annotations

from pathlib import Path
import json
import sys
from collections import Counter, defaultdict

EDGE_FIXTURES = (
    "morale_retreat_pursuit",
    "spell_disruption_edge",
    "grapple_escape",
    "swallow_cut_out",
    "cover_take_cover_missile_duel",
)


def _run_fixture(fx_dir: Path) -> tuple[bool, str, dict[str, object]]:
    from sww.game import Game
    from sww.save_load import apply_game_dict
    from sww.replay_runner import run_replay
    from sww.selftest import DummyUI
    from sww.scripted_ui import ScriptedUI

    base = json.loads((fx_dir / "base_save.json").read_text(encoding="utf-8"))
    rep = json.loads((fx_dir / "replay.json").read_text(encoding="utf-8"))
    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
    ui_actions = None
    if isinstance(rep.get("ui_actions"), list):
        ui_actions = rep["ui_actions"]
    elif isinstance(meta.get("ui_actions"), list):
        ui_actions = meta["ui_actions"]
    ui = ScriptedUI(actions=ui_actions, auto_default=False, echo=False) if ui_actions is not None else DummyUI()
    try:
        if hasattr(ui, "auto_combat") and ("auto_combat" in meta):
            ui.auto_combat = bool(meta.get("auto_combat"))
    except Exception:
        pass

    g = Game(ui, dice_seed=99999, wilderness_seed=99999)
    apply_game_dict(g, base)
    ok, msg = run_replay(g, rep, verify_rng=True, verify_ai=True, use_recorded_rng=True)
    audit = {}
    try:
        audit = g.combat_audit_snapshot()
    except Exception:
        audit = {}
    return ok, msg, audit


def main(argv: list[str]) -> int:
    from sww.fixtures_runner import default_fixtures_root

    root = default_fixtures_root()
    fixture_names = tuple(argv) if argv else EDGE_FIXTURES
    total_counts: Counter[str] = Counter()
    total_details: dict[str, Counter[str]] = defaultdict(Counter)
    failures = 0

    for name in fixture_names:
        fx_dir = Path(root) / name
        if not fx_dir.exists():
            print(f"[combat-audit] MISSING {name}")
            failures += 1
            continue
        ok, msg, audit = _run_fixture(fx_dir)
        if not ok:
            print(f"[combat-audit] FAIL {name}: {msg}")
            failures += 1
            continue
        counts = Counter({str(k): int(v) for k, v in (audit.get("counts") or {}).items()})
        details_raw = audit.get("details") or {}
        print(f"[combat-audit] PASS {name}")
        if counts:
            for k in sorted(counts):
                print(f"  - {k}: {counts[k]}")
        else:
            print("  - no audit events recorded")
        total_counts.update(counts)
        for dk, bucket in details_raw.items():
            total_details[str(dk)].update({str(k): int(v) for k, v in (bucket or {}).items()})

    if failures:
        return 1

    print("[combat-audit] aggregate counts")
    for k in sorted(total_counts):
        print(f"  - {k}: {total_counts[k]}")
    for dk in sorted(total_details):
        print(f"[combat-audit] {dk}")
        for key, value in sorted(total_details[dk].items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    - {key}: {value}")
    if not total_counts:
        print("[combat-audit] no legality rewrites were triggered by the audited fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

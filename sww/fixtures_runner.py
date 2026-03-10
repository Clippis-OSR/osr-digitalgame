"""Golden replay fixtures (regression wall).

Fixtures live under tests/fixtures/replays/<fixture_name>/ and contain:
  - base_save.json   (Game state BEFORE commands)
  - replay.json      (ReplayLog dict with commands + rng_events + ai_events + optional UI script)
  - expected.json    (Blessed hashes)

We re-apply base_save to a fresh Game, run the replay in *strict* mode
(consume recorded RNG stream), then compare hashes.

This module is intentionally deterministic: fixtures must provide any
interactive choices via scripted UI actions, otherwise we fall back to
DummyUI (which may diverge from the recording).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
import hashlib
import json


@dataclass(frozen=True)
class FixtureResult:
    name: str
    ok: bool
    message: str


def _stable_hash(obj: Any) -> str:
    # Stable JSON hash: key order, no whitespace, ASCII.
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_ui_actions(rep: dict) -> Optional[list]:
    """
    Back-compat extractor for scripted UI actions.

    We support multiple layouts because older replays stored actions in different places.
    Preferred: rep["meta"]["ui_actions"] (list)
    Also accepted: rep["ui_actions"] (list), rep["ui"]["actions"] (list)
    """
    if isinstance(rep.get("ui_actions"), list):
        return rep["ui_actions"]
    meta = rep.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("ui_actions"), list):
        return meta["ui_actions"]
    ui = rep.get("ui")
    if isinstance(ui, dict) and isinstance(ui.get("actions"), list):
        return ui["actions"]
    return None


def run_fixture_dir(fx_dir: Path) -> FixtureResult:
    # Local imports keep import-time side effects minimal.
    from .game import Game
    from .save_load import apply_game_dict
    from .replay_runner import run_replay
    from .debug_diff import snapshot_state
    from .selftest import DummyUI
    from .scripted_ui import ScriptedUI

    name = fx_dir.name
    base_p = fx_dir / "base_save.json"
    rep_p = fx_dir / "replay.json"
    exp_p = fx_dir / "expected.json"

    if not base_p.exists() or not rep_p.exists():
        return FixtureResult(name, False, "missing base_save.json or replay.json")
    if not exp_p.exists():
        return FixtureResult(name, False, "missing expected.json (run scripts/bless_fixture.py)")

    base = _load_json(base_p)
    rep = _load_json(rep_p)
    exp = _load_json(exp_p)

    if not isinstance(rep, dict):
        return FixtureResult(name, False, "replay.json is not an object")

    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}

    ui_actions = _extract_ui_actions(rep)
    if ui_actions is not None:
        ui = ScriptedUI(actions=ui_actions, auto_default=False, echo=False)
    else:
        # NOTE: Without scripted UI, combat/menus may choose defaults and diverge.
        ui = DummyUI()

    # Allow fixtures to force manual combat prompts (for pursuit/cover/etc).
    try:
        if hasattr(ui, "auto_combat") and ("auto_combat" in meta):
            ui.auto_combat = bool(meta.get("auto_combat"))
    except Exception:
        pass

    # Seeds are irrelevant when we consume recorded RNG, but keep deterministic defaults.
    g = Game(ui, dice_seed=99999, wilderness_seed=99999)
    apply_game_dict(g, base)

    ok, msg = run_replay(g, rep, verify_rng=True, verify_ai=True, use_recorded_rng=True)
    if not ok:
        return FixtureResult(name, False, f"replay failed: {msg}")

    snap = snapshot_state(g)
    got = {
        "snapshot_sha256": _stable_hash(snap),
        "rng_sha256": _stable_hash(rep.get("rng_events") or []),
        "ai_sha256": _stable_hash(rep.get("ai_events") or []),
    }

    for k in ("snapshot_sha256", "rng_sha256", "ai_sha256"):
        if str(exp.get(k) or "") != str(got.get(k) or ""):
            return FixtureResult(name, False, f"{k} mismatch")

    return FixtureResult(name, True, "ok")


def run_all_fixtures(root: Path, *, only: Optional[Iterable[str]] = None) -> tuple[bool, list[FixtureResult]]:
    """
    Run every fixture directory under root.

    If `only` is provided, only run fixtures whose directory name is in that set.
    """
    if not root.exists() or not root.is_dir():
        return True, []

    only_set = set(only) if only is not None else None

    results: list[FixtureResult] = []
    for fx in sorted([p for p in root.iterdir() if p.is_dir()]):
        if only_set is not None and fx.name not in only_set:
            continue
        results.append(run_fixture_dir(fx))

    ok = all(r.ok for r in results) if results else True
    return ok, results


def default_fixtures_root() -> Path:
    # sww/fixtures_runner.py -> project root is two levels up.
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "replays"

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional


@dataclass(frozen=True)
class EquivalenceResult:
    ok: bool
    message: str
    snapshot_equal: bool
    rng_equal: bool
    ai_equal: bool
    commands_equal: bool



def _extract_ui_actions(rep: dict[str, Any]) -> list[Any]:
    if isinstance(rep.get("ui_actions"), list):
        return list(rep.get("ui_actions") or [])
    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
    if isinstance(meta.get("ui_actions"), list):
        return list(meta.get("ui_actions") or [])
    ui = rep.get("ui") if isinstance(rep.get("ui"), dict) else {}
    if isinstance(ui.get("actions"), list):
        return list(ui.get("actions") or [])
    return []



def _auto_combat(rep: dict[str, Any]) -> bool:
    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
    return bool(meta.get("auto_combat", True))



def _command_dicts(rep: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in list(rep.get("commands") or []):
        if not isinstance(entry, dict):
            continue
        cdict = entry.get("cmd") if isinstance(entry.get("cmd"), dict) else entry
        if isinstance(cdict, dict) and str(cdict.get("type") or "").strip():
            out.append(dict(cdict))
    return out



def _mk_ui(rep: dict[str, Any], actions: Optional[list[Any]] = None):
    from .scripted_ui import ScriptedUI

    ui = ScriptedUI(actions=_extract_ui_actions(rep) if actions is None else actions, auto_default=True, echo=False)
    try:
        ui.auto_combat = _auto_combat(rep)
    except Exception:
        pass
    return ui



def _mk_game_from_base(base: dict[str, Any], rep: dict[str, Any]):
    from .game import Game
    from .save_load import apply_game_dict

    ui = _mk_ui(rep)
    g = Game(ui, dice_seed=99999, wilderness_seed=99999)
    g.enable_validation = True
    apply_game_dict(g, base)
    return g



def _dispatch_commands(game: Any, command_dicts: list[dict[str, Any]]) -> None:
    from .replay_runner import deserialize_command

    for cdict in command_dicts:
        game.dispatch(deserialize_command(cdict))



def run_save_load_equivalence(
    base: dict[str, Any],
    replay: dict[str, Any],
    *,
    split_after: int,
) -> EquivalenceResult:
    """Compare uninterrupted replay against a save/load-continued replay.

    split_after is the number of commands to execute before performing the
    save/load branch point.
    """
    from .debug_diff import snapshot_state
    from .save_load import save_game, load_game

    commands = _command_dicts(replay)
    if not commands:
        return EquivalenceResult(False, "replay has no commands", False, False, False, False)

    if split_after < 0 or split_after > len(commands):
        return EquivalenceResult(False, f"invalid split_after={split_after} for {len(commands)} commands", False, False, False, False)

    # Branch A: uninterrupted.
    baseline = _mk_game_from_base(base, replay)
    _dispatch_commands(baseline, commands)
    snap_a = snapshot_state(baseline)

    # Branch B: execute prefix, save/load, resume.
    branch = _mk_game_from_base(base, replay)
    _dispatch_commands(branch, commands[:split_after])

    remaining_actions: list[Any]
    try:
        remaining_actions = list(getattr(getattr(branch, "ui", None), "remaining_actions", lambda: [])())
    except Exception:
        remaining_actions = []

    with TemporaryDirectory() as td:
        save_path = str(Path(td) / "equivalence_save.json")
        save_game(branch, save_path)

        resumed_ui = _mk_ui(replay, actions=remaining_actions)
        resumed = type(branch)(resumed_ui, dice_seed=1, wilderness_seed=1)
        resumed.enable_validation = True
        load_game(resumed, save_path)
        _dispatch_commands(resumed, commands[split_after:])

    snap_b = snapshot_state(resumed)

    rep_a = getattr(baseline, "replay", None)
    rep_b = getattr(resumed, "replay", None)
    cmd_a = list(getattr(rep_a, "commands", []) or [])
    cmd_b = list(getattr(rep_b, "commands", []) or [])
    rng_a = list(getattr(rep_a, "rng_events", []) or [])
    rng_b = list(getattr(rep_b, "rng_events", []) or [])
    ai_a = list(getattr(rep_a, "ai_events", []) or [])
    ai_b = list(getattr(rep_b, "ai_events", []) or [])

    snapshot_equal = snap_a == snap_b
    commands_equal = cmd_a == cmd_b
    rng_equal = rng_a == rng_b
    ai_equal = ai_a == ai_b

    if snapshot_equal and commands_equal and rng_equal and ai_equal:
        return EquivalenceResult(True, "ok", True, True, True, True)

    parts: list[str] = []
    if not snapshot_equal:
        parts.append("snapshot divergence")
    if not commands_equal:
        parts.append(f"command log divergence ({len(cmd_a)} != {len(cmd_b)} or different content)")
    if not rng_equal:
        parts.append(f"rng divergence ({len(rng_a)} != {len(rng_b)} or different content)")
    if not ai_equal:
        parts.append(f"ai divergence ({len(ai_a)} != {len(ai_b)} or different content)")
    return EquivalenceResult(False, "; ".join(parts), snapshot_equal, rng_equal, ai_equal, commands_equal)



def run_equivalence_fixture_dir(fx_dir: Path, *, split_after: Optional[int] = None) -> EquivalenceResult:
    import json

    base_p = fx_dir / "base_save.json"
    rep_p = fx_dir / "replay.json"
    if not base_p.exists() or not rep_p.exists():
        return EquivalenceResult(False, "missing base_save.json or replay.json", False, False, False, False)

    base = json.loads(base_p.read_text(encoding="utf-8"))
    rep = json.loads(rep_p.read_text(encoding="utf-8"))
    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
    split = split_after
    if split is None:
        try:
            split = int(meta.get("save_load_split_after")) if meta.get("save_load_split_after") is not None else None
        except Exception:
            split = None
    if split is None:
        split = max(1, len(_command_dicts(rep)) // 2)
    return run_save_load_equivalence(base, rep, split_after=int(split))

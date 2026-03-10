"""Long-run deterministic campaign stress harness.

This is intentionally *not* a golden-fixture script.

It repeatedly drives the game via ScriptedUI using "smart enough" choices
to exercise multi-system interactions:

- dungeon time/turn coupling
- wandering checks + light consumption
- loot pickup prompting + encumbrance gating
- rest loop + spell preparation/memorization template rebuild
- save/reload determinism checks

Run:
  python -m scripts.run_campaign_sim

Recommended strict envelope:
  STRICT_CONTENT=true STRICT_REPLAY=true ASSERT_INVARIANTS=true INVARIANTS_STRICT=true EVENT_TRACE=true \
    python -m scripts.run_campaign_sim
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from sww.commands import (
    CampToMorning,
    EnterDungeon,
    DungeonAdvanceTurn,
    DungeonListen,
    DungeonSearchSecret,
    DungeonSearchTraps,
    DungeonPrepareSpells,
    DungeonCastSpell,
    DungeonLightTorch,
    DungeonLeave,
    StepTowardTown,
    TestCombat,
    TestSaveReload,
)
from sww.game import Game
from sww.scripted_ui import ScriptedUI


@dataclass(frozen=True)
class RunConfig:
    runs: int = 50
    seed0: int = 10_000

    # How much to churn the dungeon per run.
    dungeon_steps_per_leg: int = 220
    dungeon_legs: int = 2  # leave + re-enter to stress the town/rest loop

    # Wilderness churn between dungeon legs.
    wilderness_steps_per_leg: int = 30

    # Combat injection. Keep this modest; dungeon_steps already triggers fights.
    scripted_combats_per_leg: int = 3

    # Save/reload checkpoints during each dungeon leg.
    save_reload_steps: tuple[int, ...] = (40, 120, 200)


@dataclass(frozen=True)
class Contract:
    """What the harness considers PASS/FAIL.

    Hard failures:
      - any exception/invariant/assert failure
      - replay/save roundtrip failure (these surface as exceptions)

    Soft failures (recorded; optionally treated as hard with --fail-on-soft):
      - suspected "stuck" state (state fingerprint repeats for too long)
      - too many "overloaded cannot travel" errors in a row
    """

    stuck_repeat_threshold: int = 60
    overloaded_travel_error_threshold: int = 8


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def smart_choose(title: str, options: list[str]) -> int:
    """Heuristic chooser for ScriptedUI.

    This is deliberately keyword-based, so it can survive minor UI text changes.
    """

    t = _norm(title)
    opts = [_norm(o) for o in options]

    # 1) Encumbrance / overload guardrails.
    # Prefer options that *avoid* becoming overloaded.
    if any("over" in o or "overloaded" in o for o in opts) and (
        "encumbr" in t or "carry" in t or "loot" in t or "pickup" in t
    ):
        # Prefer decline/leave/drop actions if present.
        for i, o in enumerate(opts):
            if any(k in o for k in ("decline", "leave", "not now", "cancel", "drop")):
                return i

    # 2) Exercise ground-loot persistence: sometimes decline loot even if OK.
    # The decline rate is configurable by scenario (see make_smart_choose).
    if "loot" in t or "treasure" in t or "pickup" in t:
        loot_roll = (sum(ord(ch) for ch in (t + "|" + "|".join(opts))) % 10) / 10.0
        if loot_roll < 0.30:
            for i, o in enumerate(opts):
                if any(k in o for k in ("decline", "leave", "not now", "later")):
                    return i

    # 3) Prefer "continue" / "ok" over "back" if both are present.
    for i, o in enumerate(opts):
        if o in ("continue", "ok", "yes", "confirm"):
            return i

    # 4) Avoid destructive choices.
    for i, o in enumerate(opts):
        if any(k in o for k in ("abandon", "delete", "erase", "suicide", "give up")):
            # if we find a non-destructive alternative, take it
            for j, o2 in enumerate(opts):
                if not any(k in o2 for k in ("abandon", "delete", "erase", "suicide", "give up")):
                    return j
            return 0

    # 5) If we see a numbered menu and "(recommended)" in options, take it.
    for i, o in enumerate(opts):
        if "recommended" in o:
            return i

    # Fallback: first option.
    return 0


def smart_prompt(text: str, default: Optional[str]) -> str:
    """Heuristic responder for ScriptedUI.prompt."""

    t = _norm(text)

    # If asked for a character/party name etc., keep it deterministic.
    if any(k in t for k in ("name", "filename", "save", "profile")):
        return default or "auto"

    # For numeric prompts, accept default when available.
    if any(k in t for k in ("enter", "number", "amount", "how many")):
        return default or ""

    return default or ""


@dataclass(frozen=True)
class Scenario:
    """Coverage knobs.

    Scenarios are about forcing cross-system interactions, not optimal play.
    """

    name: str

    # Probability of declining loot prompts even when we could take it.
    loot_decline_rate: float = 0.30

    # Action selection knobs inside the dungeon.
    p_listen: float = 0.05
    p_secret: float = 0.03
    p_traps: float = 0.03
    p_light_torch: float = 0.03
    p_prepare_spells: float = 0.04
    p_cast_spell: float = 0.04

    # Save/reload churn: if > 0, add additional checkpoints every N turns.
    extra_save_reload_every: int = 0


def scenario_by_name(name: str) -> Scenario:
    n = _norm(name)
    if n in ("", "default", "auto", "mixed"):
        return Scenario(name="default")
    if n in ("spells", "spell", "caster", "casters"):
        return Scenario(
            name="spells",
            loot_decline_rate=0.20,
            p_prepare_spells=0.10,
            p_cast_spell=0.10,
        )
    if n in ("endurance", "push"):
        return Scenario(name="endurance", loot_decline_rate=0.25, p_light_torch=0.01)
    if n in ("greedy", "loot"):
        return Scenario(name="greedy", loot_decline_rate=0.05)
    if n in ("persistence", "save", "saveload"):
        return Scenario(name="persistence", loot_decline_rate=0.25, extra_save_reload_every=20)
    return Scenario(name="default")


def make_smart_choose(*, scenario: Scenario, rng):
    """Create a chooser tuned to a scenario."""

    def _choose(title: str, options: list[str]) -> int:
        t = _norm(title)
        opts = [_norm(o) for o in options]

        # 1) Encumbrance / overload guardrails.
        # Prefer options that resolve overload.
        if any("over" in o or "overloaded" in o for o in opts) and (
            "encumbr" in t or "carry" in t or "loot" in t or "pickup" in t
        ):
            for i, o in enumerate(opts):
                if "drop" in o:
                    return i
            for i, o in enumerate(opts):
                if any(k in o for k in ("decline", "leave", "not now", "cancel")):
                    return i

        # 2) Ground-loot persistence (decline sometimes).
        if "loot" in t or "treasure" in t or "pickup" in t:
            if rng.random() < float(scenario.loot_decline_rate):
                for i, o in enumerate(opts):
                    if any(k in o for k in ("decline", "leave", "not now", "later")):
                        return i

        # 3) Prefer "continue" / "ok".
        for i, o in enumerate(opts):
            if o in ("continue", "ok", "yes", "confirm"):
                return i

        # 4) Avoid destructive choices.
        for i, o in enumerate(opts):
            if any(k in o for k in ("abandon", "delete", "erase", "suicide", "give up")):
                for j, o2 in enumerate(opts):
                    if not any(k in o2 for k in ("abandon", "delete", "erase", "suicide", "give up")):
                        return j
                return 0

        # 5) If we see a "recommended" marker, take it.
        for i, o in enumerate(opts):
            if "recommended" in o:
                return i

        return 0

    return _choose


def _artifact_dir() -> Path:
    out = Path("artifacts") / "campaign_sim"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _seed_suite_path() -> Path:
    return Path("scripts") / "campaign_seed_suites.json"


def _load_seed_suites() -> dict:
    p = _seed_suite_path()
    if not p.exists():
        return {"golden_pass": [], "regression_fail": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"golden_pass": [], "regression_fail": []}
        data.setdefault("golden_pass", [])
        data.setdefault("regression_fail", [])
        return data
    except Exception:
        return {"golden_pass": [], "regression_fail": []}


def _save_seed_suites(data: dict) -> None:
    p = _seed_suite_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _dedupe_ints(xs) -> list[int]:
    out: list[int] = []
    seen = set()
    for x in xs or []:
        try:
            n = int(x)
        except Exception:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _state_fingerprint(g: Game) -> str:
    """Coarse fingerprint used to detect "stuck" loops.

    Keep this intentionally low-detail (stable across innocuous state changes)
    but sensitive to meaningful progress.
    """
    try:
        enc = g.party_encumbrance()
        enc_state = getattr(enc, "state", "")
        used = int(getattr(enc, "used_slots", 0) or 0)
        cap = int(getattr(enc, "capacity_slots", 0) or 0)
    except Exception:
        enc_state, used, cap = "", 0, 0

    pcs_alive = 0
    try:
        pcs_alive = len([m for m in g.party.living() if getattr(m, "is_pc", False)])
    except Exception:
        pcs_alive = 0

    parts = (
        f"day={int(getattr(g,'campaign_day',0) or 0)}",
        f"watch={int(getattr(g,'day_watch',0) or 0)}",
        f"hex={getattr(g,'party_hex',None)}",
        f"dturn={int(getattr(g,'dungeon_turn',0) or 0)}",
        f"lvl={int(getattr(g,'dungeon_level',0) or 0)}",
        f"room={int(getattr(g,'current_room_id',0) or 0)}",
        f"gold={int(getattr(g,'gold',0) or 0)}",
        f"pcs={pcs_alive}",
        f"enc={enc_state}:{used}/{cap}",
    )
    return "|".join(str(p) for p in parts)


def _snapshot_game(g: Game) -> dict:
    """Lightweight triage snapshot included in failure bundles."""

    def actor_summary(a) -> dict:
        try:
            return {
                "name": getattr(a, "name", ""),
                "class": getattr(a, "class_name", getattr(a, "klass", "")),
                "level": int(getattr(a, "level", 0) or 0),
                "hp": int(getattr(a, "hp", 0) or 0),
                "hp_max": int(getattr(a, "hp_max", 0) or 0),
                "is_pc": bool(getattr(a, "is_pc", False)),
                "is_retainer": bool(getattr(a, "is_retainer", False)),
                "status": getattr(a, "status", None),
            }
        except Exception:
            return {"name": str(a)}

    try:
        enc = g.party_encumbrance()
        enc_snap = {
            "state": getattr(enc, "state", None),
            "used_slots": int(getattr(enc, "used_slots", 0) or 0),
            "capacity_slots": int(getattr(enc, "capacity_slots", 0) or 0),
            "heavy_threshold": int(getattr(enc, "heavy_threshold", 0) or 0),
            "overloaded_threshold": int(getattr(enc, "overloaded_threshold", 0) or 0),
        }
    except Exception:
        enc_snap = None

    try:
        members = [actor_summary(m) for m in getattr(g.party, "members", [])]
    except Exception:
        members = []

    return {
        "campaign_day": int(getattr(g, "campaign_day", 0) or 0),
        "day_watch": int(getattr(g, "day_watch", 0) or 0),
        "party_hex": list(getattr(g, "party_hex", (0, 0)) or (0, 0)),
        "dungeon_turn": int(getattr(g, "dungeon_turn", 0) or 0),
        "dungeon_level": int(getattr(g, "dungeon_level", 0) or 0),
        "current_room_id": int(getattr(g, "current_room_id", 0) or 0),
        "gold": int(getattr(g, "gold", 0) or 0),
        "torches": int(getattr(g, "torches", 0) or 0),
        "torch_turns_left": int(getattr(g, "torch_turns_left", 0) or 0),
        "magical_light_turns": int(getattr(g, "magical_light_turns", 0) or 0),
        "noise_level": int(getattr(g, "noise_level", 0) or 0),
        "encumbrance": enc_snap,
        "party_members": members,
        "party_items_count": len(getattr(g, "party_items", []) or []),
    }


def _write_failure_bundle_old(*, out_dir: Path, run_idx: int, cfg: RunConfig, seeds: dict, ui: ScriptedUI, err: Exception):
    payload = {
        "run_idx": run_idx,
        "config": asdict(cfg),
        "seeds": seeds,
        "env": {k: os.environ.get(k) for k in (
            "STRICT_CONTENT",
            "STRICT_REPLAY",
            "ASSERT_INVARIANTS",
            "INVARIANTS_STRICT",
            "EVENT_TRACE",
            "EVENT_TRACE_PATH",
            "EVENT_TRACE_LEVEL",
        )},
        "error": {"type": type(err).__name__, "message": str(err)},
        "transcript": [
            {"kind": step.kind, "value": step.value} for step in ui.transcript
        ],
    }
    p = out_dir / f"fail_run_{run_idx:04d}.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _inject_scripted_combats(g: Game, leg: int, count: int, *, profile: str) -> None:
    # These monster ids exist in data/monsters.json in P4_10_47.
    # Keep this list limited to monsters known to exist in your data set.
    # Vary composition a little by profile so we hit different specials/AC/HD bands.
    base = [
        ("goblin", 6),
        ("rat_giant", 8),
        ("skeleton", 6),
        ("orc", 5),
        ("human_bandit", 6),
    ]
    tougher = [
        ("ogre", 2),
        ("ghoul", 4),
        ("wolf", 6),
    ]
    roster = list(base)
    if profile in ("spells", "endurance"):
        roster = base + tougher
    for i in range(count):
        mid, n = roster[(leg + i) % len(roster)]
        g.dispatch(TestCombat(monster_id=mid, count=n))
        g.dispatch(DungeonAdvanceTurn())


def _run_one_core(
    *,
    run_idx: int,
    cfg: RunConfig,
    out_dir: Path,
    g: Game,
    ui: ScriptedUI,
    harness_seed: int,
    profile: str,
    scenario: Scenario,
    contract: Contract,
    soft_failures: list[dict],
) -> None:
    """Execute a single run using the provided Game/UI and seeds."""
    harness_rng = __import__("random").Random(harness_seed)

    # Soft-failure watchdogs
    last_fp = None
    repeat_fp = 0
    overloaded_travel_errors = 0

    # Start from a clean "morning" state.
    g.dispatch(CampToMorning())

    for leg in range(cfg.dungeon_legs):
        g.dispatch(EnterDungeon())

        _inject_scripted_combats(g, leg=leg, count=cfg.scripted_combats_per_leg, profile=profile)

        for step in range(cfg.dungeon_steps_per_leg):
            r = harness_rng.random()
            p0 = 0.0
            p1 = p0 + float(scenario.p_listen)
            p2 = p1 + float(scenario.p_secret)
            p3 = p2 + float(scenario.p_traps)
            p4 = p3 + float(scenario.p_light_torch)
            p5 = p4 + float(scenario.p_prepare_spells)
            p6 = p5 + float(scenario.p_cast_spell)

            if r < p1:
                g.dispatch(DungeonListen())
            elif r < p2:
                g.dispatch(DungeonSearchSecret())
            elif r < p3:
                g.dispatch(DungeonSearchTraps())
            elif r < p4:
                g.dispatch(DungeonLightTorch())
            elif r < p5 and (profile in ("spells", "mixed") or scenario.name == "spells"):
                g.dispatch(DungeonPrepareSpells())
            elif r < p6 and (profile in ("spells", "mixed") or scenario.name == "spells"):
                g.dispatch(DungeonCastSpell())
            else:
                g.dispatch(DungeonAdvanceTurn())

            # Soft failure: stuck detection
            fp = _state_fingerprint(g)
            if fp == last_fp:
                repeat_fp += 1
            else:
                repeat_fp = 0
                last_fp = fp
            if repeat_fp >= contract.stuck_repeat_threshold:
                soft_failures.append({
                    "kind": "stuck_fingerprint",
                    "step": step,
                    "leg": leg,
                    "fingerprint": fp,
                    "repeat": repeat_fp,
                })
                # Reset counter so we don't spam.
                repeat_fp = 0

            do_sr = step in cfg.save_reload_steps
            if scenario.extra_save_reload_every and step > 0 and step % int(scenario.extra_save_reload_every) == 0:
                do_sr = True
            if do_sr:
                g.dispatch(TestSaveReload(note=f"run{run_idx}_leg{leg}_step{step}"))

        g.dispatch(DungeonLeave())

        for w in range(cfg.wilderness_steps_per_leg):
            res = g.dispatch(StepTowardTown())
            # Soft failure: repeated overloaded travel errors
            try:
                msgs = getattr(res, "messages", None) or ()
                if any("overloaded" in _norm(m) for m in msgs):
                    overloaded_travel_errors += 1
                else:
                    overloaded_travel_errors = 0
            except Exception:
                overloaded_travel_errors = 0
            if overloaded_travel_errors >= contract.overloaded_travel_error_threshold:
                soft_failures.append({
                    "kind": "overloaded_travel_loop",
                    "leg": leg,
                    "w_step": w,
                    "repeat": overloaded_travel_errors,
                    "fingerprint": _state_fingerprint(g),
                })
                overloaded_travel_errors = 0

        g.dispatch(CampToMorning())


def _profile_for_run(harness_seed: int) -> str:
    # Deterministic "behavior" profiles to improve interaction coverage.
    profiles = ["mixed", "endurance", "spells"]
    return profiles[int(harness_seed) % len(profiles)]


def run_one(
    run_idx: int,
    cfg: RunConfig,
    *,
    out_dir: Path,
    contract: Contract,
    fail_on_soft: bool,
    record_golden: bool,
    scenario_name: str = "auto",
) -> None:
    """Normal harness execution path (smart fallbacks + auto defaults)."""
    harness_seed = cfg.seed0 + run_idx
    dice_seed = 100_000 + run_idx
    wilderness_seed = 200_000 + run_idx
    profile = _profile_for_run(harness_seed)

    # Scenario selection: default is "auto" which maps to the current profile.
    scenario_name = (scenario_name or os.environ.get("CAMPAIGN_SCENARIO", "auto")).strip()
    if _norm(scenario_name) in ("auto", "mixed"):
        scenario = scenario_by_name(profile)
    else:
        scenario = scenario_by_name(scenario_name)

    chooser = make_smart_choose(scenario=scenario, rng=__import__("random").Random(harness_seed ^ 0x5EEDFACE))

    ui = ScriptedUI(
        actions=[],
        auto_default=True,
        echo=False,
        fallback_choose=chooser,
        fallback_prompt=smart_prompt,
    )

    g = Game(ui=ui, dice_seed=dice_seed, wilderness_seed=wilderness_seed)
    soft_failures: list[dict] = []

    try:
        _run_one_core(
            run_idx=run_idx,
            cfg=cfg,
            out_dir=out_dir,
            g=g,
            ui=ui,
            harness_seed=harness_seed,
            profile=profile,
            scenario=scenario,
            contract=contract,
            soft_failures=soft_failures,
        )
        if soft_failures:
            _write_soft_bundle(
                out_dir=out_dir,
                run_idx=run_idx,
                cfg=cfg,
                seeds={
                    "harness_seed": harness_seed,
                    "dice_seed": dice_seed,
                    "wilderness_seed": wilderness_seed,
                },
                profile=profile,
                ui=ui,
                g=g,
                soft_failures=soft_failures,
            )
            if fail_on_soft:
                raise RuntimeError(f"Soft failures recorded ({len(soft_failures)}).")

        if record_golden:
            suites = _load_seed_suites()
            suites["golden_pass"] = _dedupe_ints(list(suites.get("golden_pass", [])) + [harness_seed])
            _save_seed_suites(suites)
    except Exception as e:
        _write_failure_bundle(
            out_dir=out_dir,
            run_idx=run_idx,
            cfg=cfg,
            seeds={
                "harness_seed": harness_seed,
                "dice_seed": dice_seed,
                "wilderness_seed": wilderness_seed,
            },
            profile=profile,
            ui=ui,
            g=g,
            err=e,
        )
        suites = _load_seed_suites()
        suites["regression_fail"] = _dedupe_ints(list(suites.get("regression_fail", [])) + [harness_seed])
        _save_seed_suites(suites)
        raise

def _set_run_event_trace_path(out_dir: Path, run_idx: int) -> dict[str, Optional[str]]:
    """If EVENT_TRACE is enabled, route EVENT_TRACE_PATH to a per-run file.

    Returns a dict of previous env values so the caller can restore.
    """
    prev = {
        "EVENT_TRACE_PATH": os.environ.get("EVENT_TRACE_PATH"),
    }
    if (os.environ.get("EVENT_TRACE", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        trace_path = out_dir / f"trace_run_{run_idx:04d}.jsonl"
        os.environ["EVENT_TRACE_PATH"] = str(trace_path)
    return prev


def _restore_env(prev: dict[str, Optional[str]]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _last_ui_context(ui: ScriptedUI) -> dict:
    # Find the most recent choose/prompt step for triage.
    for step in reversed(ui.transcript):
        if step.kind in ("choose", "prompt"):
            return {"kind": step.kind, "value": step.value}
    return {"kind": None, "value": None}


def _write_repro_runner(out_dir: Path, run_idx: int, fail_json: Path) -> None:
    """Write a tiny one-command repro runner for a specific failing run."""
    repro = out_dir / f"repro_run_{run_idx:04d}.py"
    template = """# Auto-generated repro runner for a campaign_sim failure.
# Usage:
#   python {repro_name}
from __future__ import annotations

from pathlib import Path

from scripts.run_campaign_sim import run_repro

FAIL_JSON = Path(r"{fail_json}")

if __name__ == "__main__":
    raise SystemExit(run_repro(FAIL_JSON))
"""
    repro.write_text(
        template.format(repro_name=repro.name, fail_json=str(fail_json).replace('\\', '\\\\')),
        encoding="utf-8",
    )


def _transcript_tail(ui: ScriptedUI, n: int = 40) -> list[dict]:
    try:
        tail = list(ui.transcript[-n:])
    except Exception:
        tail = []
    return [{"kind": step.kind, "value": step.value} for step in tail]


def _write_soft_bundle(
    *,
    out_dir: Path,
    run_idx: int,
    cfg: RunConfig,
    seeds: dict,
    profile: str,
    ui: ScriptedUI,
    g: Game,
    soft_failures: list[dict],
):
    payload = {
        "run_idx": run_idx,
        "profile": profile,
        "config": asdict(cfg),
        "seeds": seeds,
        "env": {k: os.environ.get(k) for k in (
            "STRICT_CONTENT",
            "STRICT_REPLAY",
            "ASSERT_INVARIANTS",
            "INVARIANTS_STRICT",
            "EVENT_TRACE",
            "EVENT_TRACE_PATH",
            "EVENT_TRACE_LEVEL",
        )},
        "soft_failures": soft_failures,
        "last_ui_context": _last_ui_context(ui),
        "transcript_tail": _transcript_tail(ui, n=60),
        "snapshot": _snapshot_game(g),
    }
    p = out_dir / f"soft_run_{run_idx:04d}.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_failure_bundle(
    *,
    out_dir: Path,
    run_idx: int,
    cfg: RunConfig,
    seeds: dict,
    profile: str,
    ui: ScriptedUI,
    g: Game,
    err: Exception,
):
    last_ctx = _last_ui_context(ui)
    payload = {
        "run_idx": run_idx,
        "profile": profile,
        "config": asdict(cfg),
        "seeds": seeds,
        "env": {k: os.environ.get(k) for k in (
            "STRICT_CONTENT",
            "STRICT_REPLAY",
            "ASSERT_INVARIANTS",
            "INVARIANTS_STRICT",
            "EVENT_TRACE",
            "EVENT_TRACE_PATH",
            "EVENT_TRACE_LEVEL",
        )},
        "error": {"type": type(err).__name__, "message": str(err)},
        "last_ui_context": last_ctx,
        "transcript_tail": _transcript_tail(ui, n=80),
        "snapshot": _snapshot_game(g),
        "transcript": [
            {"kind": step.kind, "value": step.value} for step in ui.transcript
        ],
    }
    p = out_dir / f"fail_run_{run_idx:04d}.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_repro_runner(out_dir, run_idx, p)


def _print_strict_env_hint() -> None:
    required = ["STRICT_CONTENT", "STRICT_REPLAY", "ASSERT_INVARIANTS", "INVARIANTS_STRICT"]
    missing = [k for k in required if (os.environ.get(k) or "").strip() == ""]
    if missing:
        print("NOTE: strict env vars not set:", ", ".join(missing))
        print("Recommended:")
        print("  STRICT_CONTENT=true STRICT_REPLAY=true ASSERT_INVARIANTS=true INVARIANTS_STRICT=true EVENT_TRACE=true \\")
        print("    python -m scripts.run_campaign_sim")


def run_repro(path: Path) -> int:
    """Replay a failure bundle by feeding its recorded transcript."""
    from sww.scripted_ui import ScriptedStep, ScriptedUIExhausted, actions_from_transcript

    data = json.loads(path.read_text(encoding="utf-8"))
    seeds = data["seeds"]
    profile = str(data.get("profile") or _profile_for_run(int(seeds.get("harness_seed", 0))))
    transcript_raw = data.get("transcript", [])
    transcript = [ScriptedStep(step["kind"], step["value"]) for step in transcript_raw]
    actions = actions_from_transcript(transcript)

    cfg = RunConfig(**data["config"])
    out_dir = _artifact_dir()
    run_idx = int(data.get("run_idx", 0))

    ui = ScriptedUI(actions=actions, auto_default=False, echo=True)

    g = Game(ui=ui, dice_seed=int(seeds["dice_seed"]), wilderness_seed=int(seeds["wilderness_seed"]))
    soft_failures: list[dict] = []
    contract = Contract()

    prev = _set_run_event_trace_path(out_dir, run_idx)
    try:
        _run_one_core(
            run_idx=run_idx,
            cfg=cfg,
            out_dir=out_dir,
            g=g,
            ui=ui,
            harness_seed=int(seeds["harness_seed"]),
            profile=profile,
            scenario=scenario_by_name(profile),
            contract=contract,
            soft_failures=soft_failures,
        )
    except ScriptedUIExhausted as e:
        print(f"REPRO: Scripted UI exhausted before failure reproduced: {e}")
        raise
    finally:
        _restore_env(prev)
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Long-run deterministic campaign stress harness.")
    parser.add_argument(
        "--mode",
        type=str,
        default=str(os.environ.get("CAMPAIGN_MODE", "soak")),
        choices=("smoke", "regression", "soak", "micro"),
        help="Run mode: smoke (fast), regression (seed suites), soak (many seeds), micro (scenario suite).",
    )
    parser.add_argument("--runs", type=int, default=int(os.environ.get("CAMPAIGN_RUNS", "50")))
    parser.add_argument("--seed0", type=int, default=int(os.environ.get("CAMPAIGN_SEED0", "10000")))
    parser.add_argument("--max-failures", type=int, default=int(os.environ.get("CAMPAIGN_MAX_FAILURES", "999999")))
    parser.add_argument("--fail-on-soft", action="store_true", help="Treat recorded soft failures as hard failures.")
    parser.add_argument("--record-golden", action="store_true", help="Record passing harness seeds into campaign_seed_suites.json.")
    parser.add_argument(
        "--scenario",
        type=str,
        default=str(os.environ.get("CAMPAIGN_SCENARIO", "auto")),
        help="Scenario preset: auto|default|spells|endurance|greedy|persistence.",
    )
    # Back-compat for earlier experiments / user muscle memory.
    parser.add_argument("--profile", type=str, default="", help="Alias for --scenario (deprecated).")
    parser.add_argument("--repro", type=str, default="", help="Path to a fail_run_XXXX.json bundle to replay.")
    # NOTE: shrink/minimization is intentionally omitted for now.
    args = parser.parse_args()

    if args.repro:
        return run_repro(Path(args.repro))
    _print_strict_env_hint()

    contract = Contract()

    scenario_name = args.scenario
    if args.profile:
        scenario_name = args.profile

    # Mode presets
    if args.mode == "smoke":
        cfg = RunConfig(
            runs=min(10, args.runs),
            seed0=args.seed0,
            dungeon_steps_per_leg=60,
            dungeon_legs=1,
            wilderness_steps_per_leg=6,
            scripted_combats_per_leg=1,
            save_reload_steps=(30,),
        )
    elif args.mode == "micro":
        # Intentionally short runs, but across scenario presets.
        cfg = RunConfig(
            runs=min(args.runs, 20),
            seed0=args.seed0,
            dungeon_steps_per_leg=90,
            dungeon_legs=2,
            wilderness_steps_per_leg=10,
            scripted_combats_per_leg=2,
            save_reload_steps=(20, 60),
        )
    else:
        cfg = RunConfig(runs=args.runs, seed0=args.seed0)

    out_dir = _artifact_dir()
    failures = 0

    # Build run plan: list of (run_idx, scenario_name)
    run_plan: list[tuple[int, str]]
    if args.mode == "regression":
        suites = _load_seed_suites()
        # Suites store harness seeds; convert to run indices relative to seed0.
        harness_seeds = _dedupe_ints(list(suites.get("golden_pass", [])) + list(suites.get("regression_fail", [])))
        run_indices = [int(hs - cfg.seed0) for hs in harness_seeds if int(hs - cfg.seed0) >= 0]
        if not run_indices:
            print("No regression seeds found in scripts/campaign_seed_suites.json. Running smoke mode instead.")
            run_indices = list(range(min(10, cfg.runs)))
        run_plan = [(i, scenario_name) for i in run_indices]
    elif args.mode == "micro":
        scenarios = ["default", "spells", "greedy", "persistence", "endurance"]
        # Keep indices deterministic and bounded.
        indices = list(range(cfg.runs))
        run_plan = [(i, scenarios[i % len(scenarios)]) for i in indices]
    else:
        run_plan = [(i, scenario_name) for i in range(cfg.runs)]

    total = len(run_plan)

    for i, sc in run_plan:
        prev = _set_run_event_trace_path(out_dir, i)
        try:
            run_one(
                i,
                cfg,
                out_dir=out_dir,
                contract=contract,
                fail_on_soft=bool(args.fail_on_soft),
                record_golden=bool(args.record_golden) and args.mode in ("soak", "regression"),
                scenario_name=sc,
            )
            if args.mode == "micro":
                print(f"RUN {i:04d} [{sc}]: OK")
            else:
                print(f"RUN {i:04d}: OK")
        except Exception as e:
            failures += 1
            # Seed triage + last UI context
            if args.mode == "micro":
                print(f"RUN {i:04d} [{sc}]: FAIL: {type(e).__name__}: {e}")
            else:
                print(f"RUN {i:04d}: FAIL: {type(e).__name__}: {e}")
            print("  seeds:",
                  f"harness={cfg.seed0 + i}, dice={100_000 + i}, wilderness={200_000 + i}")
            # The failure bundle already records last_ui_context; print a brief line here too.
            # We can't access ui here, but run_one writes the bundle. Point user at it.
            print(f"  repro: artifacts/campaign_sim/fail_run_{i:04d}.json")
            if failures >= args.max_failures:
                print(f"Stopping early due to max failures ({args.max_failures}).")
                break
        finally:
            _restore_env(prev)

    print(f"done. failures={failures}/{total}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

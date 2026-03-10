from __future__ import annotations

"""XP pacing simulation & reporting.

Simulates "expeditions" by:
  1) generating a stocked dungeon level (P4.9.2.6.5)
  2) summing monster XP across all rooms
  3) generating treasure using the engine's XP-based hoard trade-out rules

This is NOT a tactical simulator (no deaths, no resources). It's a pacing meter.

Examples:
  python -m scripts.xp_pacing_sim --runs 200 --depth 1
  GOLD_FOR_XP=true GOLD_FOR_XP_RATE=1 python -m scripts.xp_pacing_sim --runs 200
"""

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from statistics import mean, median

# Ensure repo root (p44/) is on sys.path when running as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sww.dungeon_gen.generate import generate_level
from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.treasure_runtime import roll_xp_tradeout_hoard


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.environ.get(name, "") or "").strip().lower()
    if not v:
        return bool(default)
    return v in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    k = int(round((p / 100.0) * (len(ys) - 1)))
    k = max(0, min(k, len(ys) - 1))
    return float(ys[k])


@dataclass
class RunResult:
    seed: str
    depth: int
    rooms: int
    monster_xp: int
    hoard_gp: int
    magic_items: int
    total_xp_awarded: int


def _monster_index(content_monsters: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in content_monsters:
        mid = str(m.get("monster_id", "")).strip()
        if mid:
            out[mid] = m
    return out


def simulate_once(*, seed: str, depth: int, width: int, height: int) -> RunResult:
    # Game RNG drives hoard mult + trade-outs + gem values + magic picks.
    # IMPORTANT: Python's built-in hash() is salted per process, so we must use a
    # stable hash to keep simulation results reproducible across runs.
    h = hashlib.sha256(f"xp_pacing|{seed}|{depth}".encode("utf-8")).digest()
    dice_seed = int.from_bytes(h[:4], "big") % (2**31 - 1)
    if dice_seed <= 0:
        dice_seed = 1
    g = Game(HeadlessUI(), dice_seed=dice_seed)
    mi = _monster_index(g.content.monsters)

    art = generate_level(level_id=f"level_{depth}", seed=str(seed), width=width, height=height, depth=depth)
    stocking = art.stocking or {}
    room_contents = (stocking.get("room_contents") or {}) if isinstance(stocking, dict) else {}

    total_monster_xp = 0
    total_hoard_gp = 0
    total_magic_items = 0

    # Proxy: one hoard per room containing monsters.
    for room_id, rec in room_contents.items():
        contents = (rec.get("contents") or {}) if isinstance(rec, dict) else {}
        groups = list(contents.get("monsters") or [])
        if not groups:
            continue

        room_xp = 0
        for grp in groups:
            mid = str(grp.get("monster_id") or "")
            cnt = int(grp.get("count") or 0)
            mxp = int((mi.get(mid) or {}).get("xp") or 0)
            room_xp += cnt * mxp

        total_monster_xp += room_xp

        mult = int(g.dice_rng.randint(1, 3)) + 1  # 1d3+1
        budget_gp = room_xp * mult
        loot = roll_xp_tradeout_hoard(game=g, budget_gp=budget_gp, instance_tag=f"sim_{seed}_{room_id}")
        total_hoard_gp += int(loot.gp_total)
        total_magic_items += int(len(loot.magic_items or []))

    gold_for_xp = _env_bool("GOLD_FOR_XP", False)
    rate = _env_float("GOLD_FOR_XP_RATE", 1.0)
    xp_from_gold = int(total_hoard_gp * rate) if gold_for_xp else 0

    total_xp = int(total_monster_xp) + int(xp_from_gold)

    return RunResult(
        seed=str(seed),
        depth=int(depth),
        rooms=int(len(room_contents)),
        monster_xp=int(total_monster_xp),
        hoard_gp=int(total_hoard_gp),
        magic_items=int(total_magic_items),
        total_xp_awarded=int(total_xp),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="XP pacing simulation (stocking + XP-based hoards)")
    ap.add_argument("--runs", type=int, default=int(os.environ.get("PACING_RUNS", "200")))
    ap.add_argument("--seed-base", default=os.environ.get("PACING_SEED_BASE", "1000"))
    ap.add_argument("--depth", type=int, default=int(os.environ.get("PACING_DEPTH", "1")))
    ap.add_argument(
        "--campaign",
        default=os.environ.get("PACING_CAMPAIGN", ""),
        help=(
            "Optional campaign mix. Format: '1:10,2:8,3:6' meaning 10 expeditions at depth 1, "
            "8 at depth 2, 6 at depth 3 per campaign run. If set, overrides --depth."
        ),
    )
    ap.add_argument(
        "--retainers",
        type=int,
        default=int(os.environ.get("PACING_RETAINERS", "0")),
        choices=[0, 2, 4],
        help="Number of retainers (0/2/4). Each retainer receives 1/2 share; 2 retainers = 1 full share.",
    )
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=60)
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    runs = max(1, int(args.runs))
    depth = max(1, int(args.depth))
    seed_base = str(args.seed_base)

    def _parse_campaign(spec: str) -> list[tuple[int, int]]:
        spec = (spec or "").strip()
        if not spec:
            return []
        out: list[tuple[int, int]] = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                raise SystemExit(f"Invalid --campaign part: '{part}'. Expected 'depth:count'.")
            d_s, n_s = part.split(":", 1)
            d = int(d_s.strip())
            n = int(n_s.strip())
            if d <= 0 or n <= 0:
                raise SystemExit(f"Invalid --campaign part: '{part}'. Depth and count must be > 0.")
            out.append((d, n))
        return out

    campaign = _parse_campaign(str(args.campaign))

    results: list[RunResult] = []
    # If campaign mix is supplied, we simulate a whole campaign per run (multiple expeditions).
    # Otherwise, we simulate a single expedition at --depth.
    if not campaign:
        for i in range(runs):
            seed = f"{seed_base}:{i}"
            results.append(simulate_once(seed=seed, depth=depth, width=int(args.width), height=int(args.height)))
    else:
        for i in range(runs):
            monster_xp = 0
            hoard_gp = 0
            magic_items = 0
            total_xp_awarded = 0
            rooms = 0
            exp_idx = 0
            for d, n in campaign:
                for _ in range(n):
                    seed = f"{seed_base}:{i}:{exp_idx}:{d}"
                    r = simulate_once(seed=seed, depth=d, width=int(args.width), height=int(args.height))
                    exp_idx += 1
                    monster_xp += int(r.monster_xp)
                    hoard_gp += int(r.hoard_gp)
                    magic_items += int(r.magic_items)
                    total_xp_awarded += int(r.total_xp_awarded)
                    rooms += int(r.rooms)
            results.append(
                RunResult(
                    seed=f"{seed_base}:{i}",
                    depth=-1,  # campaign
                    rooms=rooms,
                    monster_xp=monster_xp,
                    hoard_gp=hoard_gp,
                    magic_items=magic_items,
                    total_xp_awarded=total_xp_awarded,
                )
            )

    xs_total = [float(r.total_xp_awarded) for r in results]
    xs_monster = [float(r.monster_xp) for r in results]
    xs_gp = [float(r.hoard_gp) for r in results]
    xs_magic = [float(r.magic_items) for r in results]

    gold_for_xp = _env_bool("GOLD_FOR_XP", False)
    rate = _env_float("GOLD_FOR_XP_RATE", 1.0)

    print("XP pacing simulation")
    print(f"  runs: {runs}")
    if not campaign:
        print(f"  depth: {depth}")
    else:
        mix = ", ".join([f"d{d}x{n}" for d, n in campaign])
        print(f"  campaign: {mix}")
    print(f"  gold_for_xp: {gold_for_xp} (rate={rate})")
    print(f"  retainers: {int(args.retainers)}")
    print("")
    print("Totals")
    if campaign:
        print("  (per run = one full campaign sequence)")
    else:
        print("  (per run = one full stocked level)")
    print(f"  monster_xp: mean={mean(xs_monster):.1f} med={median(xs_monster):.1f} p80={_pct(xs_monster,80):.1f}")
    print(f"  hoard_gp:   mean={mean(xs_gp):.1f} med={median(xs_gp):.1f} p80={_pct(xs_gp,80):.1f}")
    print(f"  magic:      mean={mean(xs_magic):.2f} med={median(xs_magic):.2f} p80={_pct(xs_magic,80):.2f}")
    print(f"  total_xp:   mean={mean(xs_total):.1f} med={median(xs_total):.1f} p80={_pct(xs_total,80):.1f}")

    # XP share model (campaign pacing assumption)
    pcs = 6
    retainers = int(args.retainers)
    units = (2 * pcs) + retainers  # each PC = 2 units, each retainer = 1 unit
    xs_pc = [float(r.total_xp_awarded) * 2.0 / float(units) for r in results]  # expected XP per PC
    xs_ret = [float(r.total_xp_awarded) * 1.0 / float(units) for r in results]  # expected XP per retainer

    print("")
    print("XP split assumptions")
    print(f"  PCs: {pcs}")
    print(f"  Retainers: {retainers} (each gets 1/2 share)")
    print(f"  Share units: {units}  (PC=2 units, retainer=1 unit)")
    print("")
    print("Per-character XP (expected, before remainder handling)")
    print(f"  per_PC_xp:       mean={mean(xs_pc):.1f} med={median(xs_pc):.1f} p80={_pct(xs_pc,80):.1f}")
    print(f"  per_retainer_xp: mean={mean(xs_ret):.1f} med={median(xs_ret):.1f} p80={_pct(xs_ret,80):.1f}")

    # Fighter benchmark.
    try:
        g0 = Game(HeadlessUI(), dice_seed=1)
        fighter_thresholds = {lvl: int(g0.xp_threshold_for_class("Fighter", lvl)) for lvl in range(2, 6)}
        m = float(mean(xs_pc)) if xs_pc else 0.0
        md = float(median(xs_pc)) if xs_pc else 0.0
        if m > 0.0:
            print("")
            if not campaign:
                print("Fighter benchmark (estimated expeditions to reach level)")
                for lvl in range(2, 6):
                    need = fighter_thresholds[lvl]
                    est_mean = need / m
                    est_med = need / md if md > 0.0 else float("inf")
                    print(f"  L{lvl}: need={need} XP  est_runs(mean)={est_mean:.1f}  est_runs(median)={est_med:.1f}")
            else:
                total_expeditions = sum(n for _, n in campaign)
                per_pc_per_exped_mean = m / float(total_expeditions) if total_expeditions > 0 else 0.0
                per_pc_per_exped_med = md / float(total_expeditions) if total_expeditions > 0 else 0.0
                print("Fighter benchmark (campaign mix, rough ETA)")
                print(f"  total_expeditions_per_campaign: {total_expeditions}")
                for lvl in range(2, 6):
                    need = fighter_thresholds[lvl]
                    est_mean = need / per_pc_per_exped_mean if per_pc_per_exped_mean > 0 else float("inf")
                    est_med = need / per_pc_per_exped_med if per_pc_per_exped_med > 0 else float("inf")
                    print(f"  L{lvl}: need={need} XP  est_expeditions(mean)={est_mean:.1f}  est_expeditions(median)={est_med:.1f}")
    except Exception:
        # Keep the sim robust even if the Game API changes.
        pass

    if args.csv:
        import csv

        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["seed", "depth", "rooms", "monster_xp", "hoard_gp", "magic_items", "total_xp_awarded"])
            for r in results:
                w.writerow([r.seed, r.depth, r.rooms, r.monster_xp, r.hoard_gp, r.magic_items, r.total_xp_awarded])
        print(f"\nWrote CSV: {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

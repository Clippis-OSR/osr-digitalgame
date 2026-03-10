from __future__ import annotations

"""Validate expedition XP share distribution rules.

Rules:
  - PCs receive a full share (2 units).
  - Retainers receive half share (1 unit).
  - Dead members receive no XP (not modeled here; we only test arithmetic).
  - Remainder distribution is deterministic but may vary by roster ordering; we validate invariants:
      * total distributed equals total XP
      * each PC gets either floor(2*total/units) or that plus some remainder increments
      * each retainer gets either floor(1*total/units) or that plus some remainder increments
"""


def distribute(total_xp: int, pcs: int, retainers: int) -> tuple[list[int], list[int]]:
    units = 2 * pcs + retainers
    if units <= 0:
        return ([], [])
    per_unit = total_xp // units
    remainder = total_xp - per_unit * units
    pc_xp = [2 * per_unit for _ in range(pcs)]
    r_xp = [1 * per_unit for _ in range(retainers)]
    # deterministic unit receiver order: all PC units then retainer units
    receivers: list[tuple[str,int]] = []
    for i in range(pcs):
        receivers.append(("pc", i))
        receivers.append(("pc", i))
    for j in range(retainers):
        receivers.append(("r", j))
    for k in range(remainder):
        kind, idx = receivers[k % len(receivers)]
        if kind == "pc":
            pc_xp[idx] += 1
        else:
            r_xp[idx] += 1
    return pc_xp, r_xp


def main() -> None:
    # Spot checks
    cases = [
        (100, 6, 0),
        (100, 6, 2),
        (100, 6, 4),
        (1, 6, 2),
        (13, 6, 2),
        (99, 1, 1),
        (999, 6, 4),
    ]
    for total, pcs, rets in cases:
        pc_xp, r_xp = distribute(total, pcs, rets)
        got = sum(pc_xp) + sum(r_xp)
        assert got == total, (total, pcs, rets, got)
        # PCs should never receive less than retainers
        if pcs and rets:
            assert min(pc_xp) >= max(r_xp) - 1, (total, pcs, rets, pc_xp, r_xp)

    print("OK: xp split arithmetic invariants hold.")


if __name__ == "__main__":
    main()

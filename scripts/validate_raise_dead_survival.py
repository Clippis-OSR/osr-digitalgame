"""Validate Raise Dead survival chance uses Constitution Table 3 (S&W Complete).

This is a lightweight structural check to prevent regressions where Raise Dead
accidentally uses saving throws instead of the Constitution-based % chance.
"""

from __future__ import annotations

from sww.rules import raise_dead_survival_pct


def main() -> None:
    assert raise_dead_survival_pct(3) == 50
    assert raise_dead_survival_pct(8) == 50
    assert raise_dead_survival_pct(9) == 75
    assert raise_dead_survival_pct(12) == 75
    assert raise_dead_survival_pct(13) == 100
    assert raise_dead_survival_pct(18) == 100
    print("OK: raise_dead_survival_pct matches Table 3 buckets (50/75/100).")


if __name__ == "__main__":
    main()

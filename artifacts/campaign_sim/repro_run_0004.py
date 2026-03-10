# Auto-generated repro runner for a campaign_sim failure.
# Usage:
#   python repro_run_0004.py
from __future__ import annotations

from pathlib import Path

from scripts.run_campaign_sim import run_repro

FAIL_JSON = Path(r"artifacts\\campaign_sim\\fail_run_0004.json")

if __name__ == "__main__":
    raise SystemExit(run_repro(FAIL_JSON))

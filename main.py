\
import sys

from sww.game import Game
from sww.ui import UI


def _run_selftest() -> int:
    """Run a lightweight, non-interactive sanity test suite."""
    from sww.selftest import run_selftest

    seed = None
    fast = False
    for a in sys.argv:
        if a.startswith("--selftest-seed="):
            try:
                seed = int(a.split("=", 1)[1])
            except Exception:
                seed = None
        elif a == "--selftest-fast":
            fast = True

    ok, report = run_selftest(seed=seed, fast=fast)
    print(report)
    return 0 if ok else 1


def _run_monster_audit(argv: list[str]) -> int:
    """Run deterministic monster library coverage + integrity report."""
    from sww.monster_audit import cli_main

    return cli_main(argv)


def _has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv


def _extract_monster_audit_json_path(argv: list[str]) -> str | None:
    # Supports:
    #   --monster-audit-json path
    #   --monster-audit-json=path
    for i, a in enumerate(argv):
        if a.startswith("--monster-audit-json="):
            return a.split("=", 1)[1].strip() or None
        if a == "--monster-audit-json":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1].strip() or None
            return None
    return None


if __name__ == "__main__":
    if "--pygame" in sys.argv or "--grid" in sys.argv:
        # Minimal pygame tactical prototype (P4.3).
        from ui_pygame.app import run_pygame

        raise SystemExit(run_pygame())
    if "--selftest" in sys.argv:
        raise SystemExit(_run_selftest())
    if "--monster-audit" in sys.argv:
        jp = _extract_monster_audit_json_path(sys.argv[1:])
        audit_argv: list[str] = []
        if _has_flag(sys.argv[1:], "--strict-content-report"):
            audit_argv.append("--strict-content-report")
        if jp:
            audit_argv.extend(["--json", jp])
        raise SystemExit(_run_monster_audit(audit_argv))
    Game(UI()).run()

#!/usr/bin/env python3
from __future__ import annotations

from sww.dungeon_noncombat import lint_authored_noncombat_content_for_ci


def main() -> int:
    issues = lint_authored_noncombat_content_for_ci()
    if not issues:
        print("noncombat content lint: OK")
        return 0
    print("noncombat content lint: FAIL")
    for i, issue in enumerate(issues, 1):
        print(
            f"{i}. [{issue.get('code')}] archetype={issue.get('archetype','?')} path={issue.get('path','?')} :: {issue.get('message','')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

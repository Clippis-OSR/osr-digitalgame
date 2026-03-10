"""Content linter: semantic checks beyond JSON schema.

Run:
  python -m scripts.content_lint
  STRICT_CONTENT=true python -m scripts.content_lint --strict
"""

from __future__ import annotations

import argparse
import os
import sys

from sww.content_lint import lint_content


def cli_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="content_lint", add_help=True)
    p.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    args = p.parse_args(argv)

    strict_env = (os.environ.get("STRICT_CONTENT", "").strip().lower() == "true")
    strict = bool(args.strict or strict_env)

    issues = lint_content(strict=strict)

    if not issues:
        print("CONTENT LINT: OK (no issues)")
        return 0

    # Print grouped by severity then code.
    issues_sorted = sorted(issues, key=lambda x: (0 if x.severity == "ERROR" else 1, x.code, x.where))
    err = 0
    warn = 0
    print("CONTENT LINT RESULTS")
    for it in issues_sorted:
        if it.severity == "ERROR":
            err += 1
        else:
            warn += 1
        print(f"[{it.severity}] {it.code} {it.where}: {it.message}")

    print("")
    print(f"Errors: {err} | Warnings: {warn} | Strict: {strict}")

    if err > 0:
        return 2
    if strict and warn > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))

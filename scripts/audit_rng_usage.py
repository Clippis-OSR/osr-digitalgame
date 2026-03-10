from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / 'sww', ROOT / 'scripts', ROOT / 'main.py')
ALLOWED = {
    Path('sww/rng.py'),
    Path('sww/dice.py'),
    Path('sww/dungeon_instance.py'),
    Path('scripts/audit_rng_usage.py'),
}
PATTERNS = (
    'import random',
    'from random import',
    'random.',
)


def iter_files():
    for item in SCAN_DIRS:
        if item.is_file() and item.suffix == '.py':
            yield item
            continue
        if item.is_dir():
            for path in item.rglob('*.py'):
                yield path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description='Audit stray random module usage outside approved RNG wrappers.')
    ap.add_argument('--strict', action='store_true', help='Exit non-zero when any violations are found.')
    args = ap.parse_args(argv)

    violations: list[str] = []
    for path in sorted(iter_files()):
        rel = path.relative_to(ROOT)
        if rel in ALLOWED:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except Exception as exc:
            violations.append(f'{rel}: unreadable ({exc})')
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            for pat in PATTERNS:
                if pat in line:
                    violations.append(f'{rel}:{lineno}: {stripped}')
                    break

    if violations:
        print('RNG audit: found unapproved random-module usage:')
        for v in violations:
            print(f'  {v}')
        return 1 if args.strict else 0

    print('RNG audit: clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

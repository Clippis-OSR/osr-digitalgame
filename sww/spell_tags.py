"""Spell tag taxonomy and helpers (P3.4).

Spell tags are *data*, used by monster spell-immunity-by-tag and other
structured rules. Runtime logic should never infer tags heuristically;
all tags must be explicitly stored on spells in data/spells.json.

This module defines the canonical tag vocabulary and a few small helpers
for validation/audit.
"""

from __future__ import annotations

from typing import Iterable


# Canonical tag vocabulary.
# Keep this list small, stable, and rules-relevant.
KNOWN_SPELL_TAGS: set[str] = {
    # Damage types
    "fire",
    "cold",
    "electric",
    "acid",
    "poison",
    "necrotic",
    "force",
    # Common effect families
    "healing",
    "charm",
    "fear",
    "sleep",
    "paralysis",
    "petrification",
    "mind_affecting",
    "illusion",
    "summoning",
    "creation",
    "teleport",
    "travel",
    "detection",
    "protection",
    "dispel",
    "control",
    # Lethal / draining magic
    "death",
    "energy_drain",
    # Catch-all (use sparingly; but ensures 100% coverage is feasible)
    "utility",
}


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for t in (tags or []):
        if not isinstance(t, str):
            continue
        tt = t.strip().lower()
        if not tt:
            continue
        if tt not in out:
            out.append(tt)
    return sorted(out)


def unknown_tags(tags: Iterable[str] | None) -> list[str]:
    ts = normalize_tags(tags)
    return sorted([t for t in ts if t not in KNOWN_SPELL_TAGS])

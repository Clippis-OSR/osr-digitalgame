"""Class gating rules that affect character creation.

These are *creation-time* constraints (race/alignment eligibility) rather than
equipment restrictions (handled in :mod:`p44.sww.class_rules`).

We keep these rules centralized so both the random character generator and the
guided party creation UI stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Eligibility:
    allowed_races: tuple[str, ...] | None = None
    allowed_alignments: tuple[str, ...] | None = None


# NOTE: These restrictions are from Swords & Wizardry Complete.
# - Non-humans cannot be Assassins, Druids, Monks, Paladins, or Rangers.
# - Thieves and Assassins must be Neutral or Chaotic.
# - Paladins must be Lawful (or revert to Fighter abilities).
CLASS_ELIGIBILITY: dict[str, Eligibility] = {
    "Assassin": Eligibility(allowed_races=("Human",), allowed_alignments=("Neutrality", "Chaos")),
    "Druid": Eligibility(allowed_races=("Human",), allowed_alignments=("Neutrality",)),
    "Monk": Eligibility(allowed_races=("Human",)),
    "Paladin": Eligibility(allowed_races=("Human",), allowed_alignments=("Law",)),
    "Ranger": Eligibility(allowed_races=("Human",)),
    "Thief": Eligibility(allowed_alignments=("Neutrality", "Chaos")),
}


def allowed_races_for_class(class_id: str) -> tuple[str, ...] | None:
    """Return allowed races for the class, or None if unrestricted."""
    e = CLASS_ELIGIBILITY.get(class_id)
    return e.allowed_races if e else None


def allowed_alignments_for_class(class_id: str) -> tuple[str, ...] | None:
    """Return allowed alignments for the class, or None if unrestricted."""
    e = CLASS_ELIGIBILITY.get(class_id)
    return e.allowed_alignments if e else None


def is_race_allowed(class_id: str, race: str) -> bool:
    allow = allowed_races_for_class(class_id)
    return True if allow is None else race in allow


def is_alignment_allowed(class_id: str, alignment: str) -> bool:
    allow = allowed_alignments_for_class(class_id)
    return True if allow is None else alignment in allow

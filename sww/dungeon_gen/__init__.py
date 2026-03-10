"""Procedural dungeon generation (P4.9.x).

This package is intentionally isolated from the main combat/grid engine:
it produces immutable generation artifacts (blueprint + report) that can
be loaded into a dungeon instance state.
"""

from __future__ import annotations

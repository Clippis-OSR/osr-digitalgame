"""Procedural dungeon generation (P4.9.x).

This package is intentionally isolated from the main combat/grid engine:
it produces immutable generation artifacts (blueprint + report) that can
be loaded into a dungeon instance state.
"""

from __future__ import annotations

from .pipeline import (
    CanonicalPipelineArtifacts,
    CanonicalPipelineValidation,
    run_canonical_blueprint_stage,
    run_canonical_level_pipeline,
    validate_canonical_blueprint,
)

from __future__ import annotations

"""Canonical dungeon generation pipeline.

Flow (P6 normalization):
  schema -> blueprint -> generator (stocking) -> instance_state -> delta persistence

This module is intentionally thin and behavior-preserving: it wraps existing
`generate_blueprint`, `generate_level`, and validation primitives into explicit
entrypoints so callers converge on one canonical orchestration path.
"""

from dataclasses import dataclass
from typing import Any

from .blueprint import DungeonBlueprint
from .generate import GenerationArtifacts, GenerationLevelArtifacts, generate_blueprint, generate_level
from .validate import validate_blueprint


@dataclass(frozen=True)
class CanonicalPipelineArtifacts:
    blueprint: DungeonBlueprint
    stocking: dict[str, Any]
    instance_state: dict[str, Any]
    report: dict[str, Any]


@dataclass(frozen=True)
class CanonicalPipelineValidation:
    passed: bool
    checks: list[dict[str, Any]]


def run_canonical_blueprint_stage(
    *,
    level_id: str,
    seed: str,
    width: int,
    height: int,
    depth: int,
    cell_size: int = 1,
    origin: tuple[int, int] = (0, 0),
    max_retries: int = 5,
) -> GenerationArtifacts:
    """Canonical schema->blueprint stage.

    Existing behavior is preserved by delegating to `generate_blueprint`.
    """
    return generate_blueprint(
        level_id=level_id,
        seed=seed,
        width=width,
        height=height,
        depth=depth,
        cell_size=cell_size,
        origin=origin,
        max_retries=max_retries,
    )


def run_canonical_level_pipeline(
    *,
    level_id: str,
    seed: str,
    width: int,
    height: int,
    depth: int,
    cell_size: int = 1,
    origin: tuple[int, int] = (0, 0),
    max_retries: int = 5,
) -> CanonicalPipelineArtifacts:
    """Canonical schema->blueprint->generator->instance pipeline.

    Returns immutable blueprint, deterministic stocking payload, and initialized
    instance_state; mutable runtime deltas are persisted later by game runtime.
    """
    art: GenerationLevelArtifacts = generate_level(
        level_id=level_id,
        seed=seed,
        width=width,
        height=height,
        depth=depth,
        cell_size=cell_size,
        origin=origin,
        max_retries=max_retries,
    )
    return CanonicalPipelineArtifacts(
        blueprint=art.blueprint,
        stocking=dict(art.stocking),
        instance_state=dict(art.instance_state),
        report=dict(art.report),
    )


def validate_canonical_blueprint(blueprint: DungeonBlueprint) -> CanonicalPipelineValidation:
    """Run the canonical connectivity/basic-invariant validation stage."""
    v = validate_blueprint(
        rooms=blueprint.rooms,
        doors=blueprint.doors,
        stairs=blueprint.stairs,
        connectivity=blueprint.connectivity,
    )
    return CanonicalPipelineValidation(passed=bool(v.passed), checks=list(v.checks))

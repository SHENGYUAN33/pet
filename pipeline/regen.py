from __future__ import annotations

import copy
import uuid

from pipeline import config
from pipeline.fact_check import find_missing_disclosures
from pipeline.pet_repo import get_generation_job, get_pet, record_generation_job
from pipeline.qa import validate_script_structure
from pipeline.rendering import render_script


def apply_scene_overrides(
    script: dict,
    scene_id: int,
    *,
    visual_source: str | None = None,
    subtitle: str | None = None,
    narration: str | None = None,
) -> dict:
    """Return a copy of script with the given scene's fields overridden
    (only fields that are not None are changed). Pure/no I/O, so this is
    the one part of single-shot regeneration that's easy to unit test."""
    patched = copy.deepcopy(script)
    for scene in patched["scenes"]:
        if scene["scene_id"] == scene_id:
            if visual_source is not None:
                scene["visual_source"] = visual_source
            if subtitle is not None:
                scene["subtitle"] = subtitle
            if narration is not None:
                scene["narration"] = narration
            return patched
    raise ValueError(f"scene_id {scene_id} not found in script")


def regenerate_scene(
    job_id: int,
    scene_id: int,
    *,
    visual_source: str | None = None,
    subtitle: str | None = None,
    narration: str | None = None,
    voice_sample: str | None = None,
    music_track: str | None = None,
) -> tuple[str, int]:
    """Re-render a whole video from job_id's script with one scene patched,
    without re-running script generation (the LLM step) — the actually
    fragile/inconsistent part per prior testing, not the FFmpeg rendering.
    All scenes are re-rendered fresh (cheap FFmpeg operations, not AI
    generation) rather than reusing the old job's clip files, to avoid the
    complexity of tracking which clips are still valid. voice_sample/
    music_track are not persisted on the job, so pass them again if the
    original generation used them. Returns (output_path, new_job_id); the
    new job's parent_job_id points back to job_id, and the original job's
    output file is left untouched."""
    job = get_generation_job(job_id)
    if job is None:
        raise ValueError(f"No generation job found with id {job_id}")

    profile = get_pet(job["pet_id"])
    if profile is None:
        raise ValueError(f"No pet found with id {job['pet_id']!r}")

    script = apply_scene_overrides(
        job["script_json"],
        scene_id,
        visual_source=visual_source,
        subtitle=subtitle,
        narration=narration,
    )

    missing = find_missing_disclosures(script, profile)
    structure_issues = validate_script_structure(script)
    script["_disclosure_check"] = {"missing_restrictions": missing}
    script["_structure_check"] = {"issues": structure_issues}
    if missing:
        print(f"[WARNING] may be missing required disclosure(s): {missing}")
    if structure_issues:
        print(f"[WARNING] structural issues: {structure_issues}")

    work_dir = config.OUTPUT_DIR / profile.pet_id / f"gen_{uuid.uuid4().hex[:8]}"
    final_path = render_script(
        profile, script, work_dir, voice_sample=voice_sample, music_track=music_track
    )

    new_job_id = record_generation_job(
        profile.pet_id,
        style=job["style"],
        duration=job["duration"],
        output_path=str(final_path),
        disclosure_missing=missing,
        structure_issues=structure_issues,
        script_json=script,
        parent_job_id=job_id,
    )

    return str(final_path), new_job_id

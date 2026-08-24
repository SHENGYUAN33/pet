from __future__ import annotations

import copy
import uuid

from pipeline import config
from pipeline.fact_check import find_missing_disclosures
from pipeline.pet_repo import (
    fail_generation_job,
    finish_generation_job,
    get_generation_job,
    get_pet,
    record_job_script,
    start_generation_job,
)
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback, noop, scaled
from pipeline.qa import validate_script_structure
from pipeline.rendering import render_script
from pipeline.scene_tracking import DatabaseSceneTracker


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
    animate: bool = False,
    video_provider: str = "svd",
    animate_prompt: str | None = None,
    on_progress: ProgressCallback = noop,
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
    on_progress("讀取原始版本", 0.02)
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

    # Opened before the render so an I2V regeneration that dies partway
    # still shows up in the pet's history as a failed attempt, linked to
    # the version it was regenerated from.
    new_job_id = start_generation_job(
        profile.pet_id,
        style=job["style"],
        duration=job["duration"],
        parent_job_id=job_id,
        voice_sample=voice_sample,
        music_track=music_track,
        animate_scenes={scene_id} if animate else None,
        video_provider=video_provider,
        animate_prompt=animate_prompt,
    )
    try:
        final_path = _render_revision(
            profile,
            script,
            scene_id,
            new_job_id=new_job_id,
            voice_sample=voice_sample,
            music_track=music_track,
            animate=animate,
            video_provider=video_provider,
            animate_prompt=animate_prompt,
            on_progress=on_progress,
        )
    except Exception as e:
        fail_generation_job(new_job_id, f"{type(e).__name__}: {e}")
        raise

    return final_path, new_job_id


def _render_revision(
    profile: PetProfile,
    script: dict,
    scene_id: int,
    *,
    new_job_id: int,
    voice_sample: str | None,
    music_track: str | None,
    animate: bool,
    video_provider: str,
    animate_prompt: str | None,
    on_progress: ProgressCallback,
) -> str:
    """The body of regenerate_scene() — split out so the job row is closed as
    FAILED by exactly one except clause."""
    missing = find_missing_disclosures(script, profile)
    structure_issues = validate_script_structure(script)
    script["_disclosure_check"] = {"missing_restrictions": missing}
    script["_structure_check"] = {"issues": structure_issues}
    if missing:
        print(f"[WARNING] may be missing required disclosure(s): {missing}")
    if structure_issues:
        print(f"[WARNING] structural issues: {structure_issues}")

    work_dir = config.OUTPUT_DIR / profile.pet_id / f"gen_{uuid.uuid4().hex[:8]}"
    record_job_script(
        new_job_id,
        script_json=script,
        work_dir=str(work_dir),
        disclosure_missing=missing,
        structure_issues=structure_issues,
    )

    final_path = render_script(
        profile,
        script,
        work_dir,
        voice_sample=voice_sample,
        music_track=music_track,
        animate_scenes={scene_id} if animate else None,
        video_provider=video_provider,
        animate_prompt=animate_prompt,
        on_progress=scaled(on_progress, 0.05, 0.98),
        scene_tracker=DatabaseSceneTracker(new_job_id),
    )

    on_progress("寫入生成紀錄", 0.99)
    finish_generation_job(new_job_id, output_path=str(final_path))

    return str(final_path)

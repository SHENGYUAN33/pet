from __future__ import annotations

import copy
import uuid

from pipeline import config
from pipeline.background import BackgroundMode
from pipeline.fact_check import (
    find_background_risks,
    find_missing_disclosures,
    find_unsupported_claims,
)
from pipeline.overlay_renderer import SceneOverlaySpec
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
from pipeline.props import SceneProp, prop_specs_from_job, prop_specs_to_job
from pipeline.qa import validate_script_structure
from pipeline.rendering import render_script
from pipeline.scene_tracking import DatabaseSceneTracker
from providers.llm.ollama_provider import OllamaLLMProvider


def apply_scene_overrides(
    script: dict,
    scene_id: int,
    *,
    visual_source: str | None = None,
    subtitle: str | None = None,
    narration: str | None = None,
    overlay: SceneOverlaySpec | None = None,
) -> dict:
    """Return a copy of script with the given scene's fields overridden
    (only fields that are not None are changed). Pure/no I/O, so this is
    the one part of single-shot regeneration that's easy to unit test.

    overlay replaces the shot's composed panel wholesale rather than merging
    field by field, because the fields belong to a template: keeping the old
    headline when the reviewer switched to a speech bubble would leave copy
    on the scene that nothing renders and fact-checking still reads. Passing
    a spec with template "none" is how a panel is taken off — the reason
    this is one nullable object instead of a set of nullable strings, which
    could only ever mean "leave it alone"."""
    patched = copy.deepcopy(script)
    for scene in patched["scenes"]:
        if scene["scene_id"] == scene_id:
            if visual_source is not None:
                scene["visual_source"] = visual_source
            if subtitle is not None:
                scene["subtitle"] = subtitle
            if narration is not None:
                scene["narration"] = narration
            if overlay is not None:
                scene["overlay"] = overlay.model_dump(mode="json")
            return patched
    raise ValueError(f"scene_id {scene_id} not found in script")


def regenerate_scene(
    job_id: int,
    scene_id: int,
    *,
    visual_source: str | None = None,
    subtitle: str | None = None,
    narration: str | None = None,
    overlay: SceneOverlaySpec | None = None,
    voice_sample: str | None = None,
    music_track: str | None = None,
    animate: bool = False,
    video_provider: str = "svd",
    animate_prompt: str | None = None,
    generate_background: bool = False,
    background_mode: BackgroundMode = BackgroundMode.EXTEND,
    image_provider: str = "comfy",
    background_prompt: str | None = None,
    props: list[SceneProp] | None = None,
    accent_colour: str | None = None,
    border_width: int | None = None,
    on_progress: ProgressCallback = noop,
) -> tuple[str, int]:
    """Re-render a whole video from job_id's script with one scene patched,
    without re-running script generation (the LLM step) — the actually
    fragile/inconsistent part per prior testing, not the FFmpeg rendering.
    All scenes are re-rendered fresh (cheap FFmpeg operations, not AI
    generation) rather than reusing the old job's clip files, to avoid the
    complexity of tracking which clips are still valid. voice_sample/
    music_track are not persisted on the job, so pass them again if the
    original generation used them — the same goes for animate and
    generate_background,
    which apply to the patched scene only, so a revision does not silently
    inherit generated content the reviewer did not ask for again.
    Returns (output_path, new_job_id); the
    new job's parent_job_id points back to job_id, and the original job's
    output file is left untouched."""
    on_progress("讀取原始版本", 0.02)
    job = get_generation_job(job_id)
    if job is None:
        raise ValueError(f"No generation job found with id {job_id}")

    profile = get_pet(job["pet_id"])
    if profile is None:
        raise ValueError(f"No pet found with id {job['pet_id']!r}")

    # Props the run already carried, kept. Unlike the overlay they do not
    # live in the script — they are a reviewer's decision about one
    # photograph, recorded on the job row — so a revision that only carried
    # the newly placed one silently stripped the collar somebody added two
    # revisions ago. A revision changes one shot; it does not undress the
    # others.
    inherited = prop_specs_from_job(job)
    if props:
        inherited[scene_id] = list(props)
    resolved_props = inherited

    script = apply_scene_overrides(
        job["script_json"],
        scene_id,
        visual_source=visual_source,
        subtitle=subtitle,
        narration=narration,
        overlay=overlay,
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
        background_scenes={scene_id} if generate_background else None,
        background_mode=background_mode.value,
        image_provider=image_provider,
        background_prompt=background_prompt,
        # Only the patched scene can carry props: a revision paints what the
        # reviewer just placed on the shot they are looking at, and a region
        # is a place on that one photograph.
        prop_specs=prop_specs_to_job(resolved_props),
        decor_accent=accent_colour,
        decor_border_width=border_width,
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
            generate_background=generate_background,
            background_mode=background_mode,
            image_provider=image_provider,
            background_prompt=background_prompt,
            props=resolved_props,
            accent_colour=accent_colour,
            border_width=border_width,
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
    generate_background: bool,
    background_mode: BackgroundMode,
    image_provider: str,
    background_prompt: str | None,
    props: dict[int, list[SceneProp]],
    accent_colour: str | None,
    border_width: int | None,
    on_progress: ProgressCallback,
) -> str:
    """The body of regenerate_scene() — split out so the job row is closed as
    FAILED by exactly one except clause."""
    # A revision is exactly where a reviewer's own wording enters the video,
    # so it is checked like any other script. This is one small call, not the
    # script generation that regeneration exists to avoid re-running.
    llm = OllamaLLMProvider()
    missing = find_missing_disclosures(script, profile, llm)
    background_risks = find_background_risks(script)
    unsupported = find_unsupported_claims(script, profile, llm)
    structure_issues = validate_script_structure(script, prop_specs=prop_specs_to_job(props))
    script["_disclosure_check"] = {
        "missing_restrictions": missing,
        "background_risks": background_risks,
        "unsupported_claims": unsupported,
    }
    script["_structure_check"] = {"issues": structure_issues}
    if missing:
        print(f"[WARNING] may be missing required disclosure(s): {missing}")
    for risk in background_risks:
        print(f"[WARNING] {risk}")
    for claim in unsupported:
        print(f"[WARNING] 影片說了資料裡沒有的事：{claim}")
    if structure_issues:
        print(f"[WARNING] structural issues: {structure_issues}")

    work_dir = config.OUTPUT_DIR / profile.pet_id / f"gen_{uuid.uuid4().hex[:8]}"
    record_job_script(
        new_job_id,
        script_json=script,
        work_dir=str(work_dir),
        disclosure_missing=missing,
        background_risks=background_risks,
        unsupported_claims=unsupported,
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
        background_scenes={scene_id} if generate_background else None,
        background_mode=background_mode,
        image_provider=image_provider,
        background_prompt=background_prompt,
        prop_specs=props or None,
        accent_colour=accent_colour,
        border_width=border_width,
        on_progress=scaled(on_progress, 0.05, 0.98),
        scene_tracker=DatabaseSceneTracker(new_job_id),
    )

    on_progress("寫入生成紀錄", 0.99)
    finish_generation_job(new_job_id, output_path=str(final_path))

    return str(final_path)

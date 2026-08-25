from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from pipeline import config
from pipeline.fact_check import find_missing_disclosures
from pipeline.models import JobStatus
from pipeline.montage import append_recap_scene
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
from pipeline.script_gen import SCRIPT_STYLES, generate_all_styles, require_media_assets
from providers.llm.ollama_provider import OllamaLLMProvider


def generate_video(
    *,
    pet_id: str,
    voice_sample: str | None = None,
    music_track: str | None = None,
    style: str = "cute",
    duration: int = 30,
    animate_scenes: set[int] | None = None,
    video_provider: str = "svd",
    animate_prompt: str | None = None,
    recap_unused_assets: bool = False,
    on_progress: ProgressCallback = noop,
) -> tuple[str, int]:
    """voice_sample=None runs without narration (silent placeholder audio
    per scene) — useful for testing script generation and video assembly
    before a TTS voice reference is available. music_track=None skips
    background music (narration/silence only). Returns (output_path, job_id)
    — job_id can later be passed to pipeline.regen.regenerate_scene().
    recap_unused_assets appends a closing quick-cut of the assets the
    script had no room for (see pipeline/montage.py) — a 30-second video is
    5-7 shots, so a pet with thirteen photos otherwise leaves six of them
    unseen. It makes the video longer by the recap's own length.
    on_progress (see pipeline/progress.py) reports the current stage; the
    CLI leaves it at the no-op default."""
    on_progress("讀取寵物資料", 0.01)
    profile = get_pet(pet_id)
    if profile is None:
        # Raised before the job row exists on purpose: an unknown pet is a
        # bad request, not a run that failed, and shouldn't litter the pet's
        # history (it has none) with a FAILED row.
        raise ValueError(
            f"No pet found with id {pet_id!r} — import it first: "
            f"python -m pipeline.manage import-profile <path>"
        )
    # Same reasoning as the check above: a pet with no media cannot be
    # scripted at all, so this is a bad request rather than a failed run.
    require_media_assets(profile)

    # Opened before the slow work so a crash or restart mid-run still leaves
    # a record; _run_generation() below closes it either way.
    job_id = start_generation_job(
        profile.pet_id,
        style=style,
        duration=duration,
        voice_sample=voice_sample,
        music_track=music_track,
        animate_scenes=animate_scenes,
        video_provider=video_provider,
        animate_prompt=animate_prompt,
    )
    try:
        final_path = _run_generation(
            profile,
            style=style,
            duration=duration,
            job_id=job_id,
            voice_sample=voice_sample,
            music_track=music_track,
            animate_scenes=animate_scenes,
            video_provider=video_provider,
            animate_prompt=animate_prompt,
            recap_unused_assets=recap_unused_assets,
            on_progress=on_progress,
        )
    except Exception as e:
        fail_generation_job(job_id, f"{type(e).__name__}: {e}")
        raise

    return final_path, job_id


def _run_generation(
    profile: PetProfile,
    *,
    style: str,
    duration: int,
    job_id: int,
    voice_sample: str | None,
    music_track: str | None,
    animate_scenes: set[int] | None,
    video_provider: str,
    animate_prompt: str | None,
    recap_unused_assets: bool,
    on_progress: ProgressCallback,
) -> str:
    """The body of generate_video() — split out so the job row is closed as
    FAILED by exactly one except clause rather than by a try block wrapped
    around the whole function."""
    llm = OllamaLLMProvider()
    # Script generation is the slowest step of a default (no-I2V) run, so it
    # reports per style rather than as one opaque block.
    scripts = generate_all_styles(
        profile, llm, duration=duration, on_progress=scaled(on_progress, 0.05, 0.33)
    )

    # storage/output/<pet_id>/scripts/ holds the latest generated scripts
    # for human browsing, shared across runs — the per-job renders below
    # each get their own gen_<token>/ directory so scene clips never collide.
    scripts_dir = config.OUTPUT_DIR / profile.pet_id / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    selected_missing: list[str] = []
    selected_structure_issues: list[str] = []
    for name, s in scripts.items():
        missing = find_missing_disclosures(s, profile)
        s["_disclosure_check"] = {"missing_restrictions": missing}
        if missing:
            print(
                f"[WARNING] style={name!r} may be missing required disclosure(s): {missing} "
                "— review before this script is approved for publish."
            )

        structure_issues = validate_script_structure(s)
        s["_structure_check"] = {"issues": structure_issues}
        if structure_issues:
            print(f"[WARNING] style={name!r} has structural issues: {structure_issues}")

        if name == style:
            selected_missing = missing
            selected_structure_issues = structure_issues

        (scripts_dir / f"{name}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    on_progress("事實與結構檢查", 0.34)
    script = scripts[style]
    if recap_unused_assets:
        # Appended here rather than inside rendering so the script recorded on
        # the job row is the one that gets rendered: resume and single-shot
        # regeneration both read the script back from there, and a recap that
        # only existed at render time would quietly disappear from both.
        script = append_recap_scene(script, profile)

    work_dir = config.OUTPUT_DIR / profile.pet_id / f"gen_{uuid.uuid4().hex[:8]}"
    # Attached before rendering, not after: resume_generation_job() needs the
    # script to know what to render and work_dir to find the clips that a
    # previous attempt already produced.
    record_job_script(
        job_id,
        script_json=script,
        work_dir=str(work_dir),
        disclosure_missing=selected_missing,
        structure_issues=selected_structure_issues,
    )

    final_path = render_script(
        profile,
        script,
        work_dir,
        voice_sample=voice_sample,
        music_track=music_track,
        animate_scenes=animate_scenes,
        video_provider=video_provider,
        animate_prompt=animate_prompt,
        on_progress=scaled(on_progress, 0.35, 0.98),
        scene_tracker=DatabaseSceneTracker(job_id),
    )

    on_progress("寫入生成紀錄", 0.99)
    finish_generation_job(job_id, output_path=str(final_path))

    return str(final_path)


def resume_generation_job(
    job_id: int,
    *,
    on_progress: ProgressCallback = noop,
) -> str:
    """Continue a run that failed partway through, reusing the scene clips it
    already finished.

    The point of per-scene jobs: a Wan2.2 scene costs about eight minutes, so
    a run that died on scene 5 must not re-render scenes 1-4. Their clips are
    still in the job's work_dir and their rows say DONE, so rendering skips
    straight past them.

    Continues the same job row rather than opening a new one — the run is the
    same attempt at the same script, and a second row would double-count it in
    the pet's history. Everything that shapes the output (script, narration
    voice, music, which scenes are animated and with what) comes from the job
    row rather than from arguments: resuming has to finish the video that was
    being made, not make a subtly different one. Returns the output path.
    """
    on_progress("讀取未完成的工作", 0.01)
    job = get_generation_job(job_id)
    if job is None:
        raise ValueError(f"No generation job found with id {job_id}")
    if job["status"] == JobStatus.DONE.value:
        raise ValueError(f"Job {job_id} already finished — nothing to resume")
    if not job["script_json"] or not job["work_dir"]:
        # It died before the script was attached, so there is nothing to
        # continue from; the only honest option is a fresh run.
        raise ValueError(
            f"Job {job_id} failed before its script was recorded — "
            "start a new generation instead of resuming"
        )

    profile = get_pet(job["pet_id"])
    if profile is None:
        raise ValueError(f"No pet found with id {job['pet_id']!r}")

    try:
        final_path = render_script(
            profile,
            job["script_json"],
            Path(job["work_dir"]),
            voice_sample=job["voice_sample"],
            music_track=job["music_track"],
            animate_scenes=set(job["animate_scenes"]) if job["animate_scenes"] else None,
            video_provider=job["video_provider"] or "svd",
            animate_prompt=job["animate_prompt"],
            on_progress=scaled(on_progress, 0.05, 0.98),
            scene_tracker=DatabaseSceneTracker(job_id, resume=True),
        )
    except Exception as e:
        fail_generation_job(job_id, f"{type(e).__name__}: {e}")
        raise

    on_progress("寫入生成紀錄", 0.99)
    finish_generation_job(job_id, output_path=str(final_path))
    return str(final_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MVP pipeline: generate a pet adoption video for a pet in the catalog"
    )
    parser.add_argument(
        "--pet-id", required=True, help="Pet id in the catalog (see pipeline.manage)"
    )
    parser.add_argument(
        "--voice-sample",
        default=None,
        help="Reference wav for TTS voice cloning; omit to skip narration (silent placeholder audio)",
    )
    parser.add_argument(
        "--music-track",
        default=None,
        help="Background music file; omit to skip music (narration/silence only)",
    )
    parser.add_argument("--style", default="cute", choices=SCRIPT_STYLES)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument(
        "--animate-scenes",
        default=None,
        help="Comma-separated scene ids to animate via Image-to-Video instead of "
        "Ken Burns (only applies to photo-sourced scenes), e.g. 2,4",
    )
    parser.add_argument(
        "--video-provider",
        default="svd",
        choices=["svd", "cogvideox", "wan"],
        help="Which open-source I2V model to use with --animate-scenes (default: svd)",
    )
    parser.add_argument(
        "--recap-unused-assets",
        action="store_true",
        help="Append a closing quick-cut of the assets the script had no room for, "
        "so every uploaded photo/video appears (lengthens the video)",
    )
    parser.add_argument(
        "--animate-prompt",
        default=None,
        help="Motion guidance for animated scenes, e.g. '貓輕輕搖尾巴、抬頭看鏡頭' "
        "(only affects prompt-conditioned providers: cogvideox, wan; ignored by svd)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    animate_scenes = (
        {int(s) for s in args.animate_scenes.split(",")} if args.animate_scenes else None
    )

    output_path, job_id = generate_video(
        pet_id=args.pet_id,
        voice_sample=args.voice_sample,
        music_track=args.music_track,
        style=args.style,
        duration=args.duration,
        animate_scenes=animate_scenes,
        video_provider=args.video_provider,
        animate_prompt=args.animate_prompt,
        recap_unused_assets=args.recap_unused_assets,
    )
    print(f"Job id: {job_id}")
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

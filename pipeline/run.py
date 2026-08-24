from __future__ import annotations

import argparse
import json
import uuid

from pipeline import config
from pipeline.fact_check import find_missing_disclosures
from pipeline.pet_repo import (
    fail_generation_job,
    finish_generation_job,
    get_pet,
    start_generation_job,
)
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback, noop, scaled
from pipeline.qa import validate_script_structure
from pipeline.rendering import render_script
from pipeline.script_gen import SCRIPT_STYLES, generate_all_styles
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
    on_progress: ProgressCallback = noop,
) -> tuple[str, int]:
    """voice_sample=None runs without narration (silent placeholder audio
    per scene) — useful for testing script generation and video assembly
    before a TTS voice reference is available. music_track=None skips
    background music (narration/silence only). Returns (output_path, job_id)
    — job_id can later be passed to pipeline.regen.regenerate_scene().
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

    # Opened before the slow work so a crash or restart mid-run still leaves
    # a record; _run_generation() below closes it either way.
    job_id = start_generation_job(profile.pet_id, style=style, duration=duration)
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

    work_dir = config.OUTPUT_DIR / profile.pet_id / f"gen_{uuid.uuid4().hex[:8]}"
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
    )

    on_progress("寫入生成紀錄", 0.99)
    finish_generation_job(
        job_id,
        output_path=str(final_path),
        disclosure_missing=selected_missing,
        structure_issues=selected_structure_issues,
        script_json=script,
    )

    return str(final_path)


def main():
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
        "--prompt",
        default=None,
        help="Motion guidance for animated scenes, e.g. '貓輕輕搖尾巴、抬頭看鏡頭' "
        "(only affects prompt-conditioned providers: cogvideox, wan; ignored by svd)",
    )
    args = parser.parse_args()

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
        animate_prompt=args.prompt,
    )
    print(f"Job id: {job_id}")
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

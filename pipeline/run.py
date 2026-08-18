from __future__ import annotations

import argparse
import json
import uuid

from pipeline import config
from pipeline.fact_check import find_missing_disclosures
from pipeline.pet_repo import get_pet, record_generation_job
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
) -> tuple[str, int]:
    """voice_sample=None runs without narration (silent placeholder audio
    per scene) — useful for testing script generation and video assembly
    before a TTS voice reference is available. music_track=None skips
    background music (narration/silence only). Returns (output_path, job_id)
    — job_id can later be passed to pipeline.regen.regenerate_scene()."""
    profile = get_pet(pet_id)
    if profile is None:
        raise ValueError(
            f"No pet found with id {pet_id!r} — import it first: "
            f"python -m pipeline.manage import-profile <path>"
        )

    llm = OllamaLLMProvider()
    scripts = generate_all_styles(profile, llm, duration=duration)

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
    )

    job_id = record_generation_job(
        profile.pet_id,
        style=style,
        duration=duration,
        output_path=str(final_path),
        disclosure_missing=selected_missing,
        structure_issues=selected_structure_issues,
        script_json=script,
    )

    return str(final_path), job_id


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

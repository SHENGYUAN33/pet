from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline import config
from pipeline.audio_mix import mix_narration_with_music
from pipeline.editing import (
    build_scene_clip,
    concat_audio,
    concat_video_only,
    mux_video_audio,
)
from pipeline.fact_check import find_missing_disclosures
from pipeline.narration import silence_scenes, synthesize_scenes
from pipeline.profile import PetProfile
from pipeline.script_gen import SCRIPT_STYLES, generate_all_styles
from providers.llm.ollama_provider import OllamaLLMProvider
from providers.tts.xtts_provider import XTTSProvider


def _resolve_visual_path(profile: PetProfile, visual_source: str) -> Path:
    """visual_source from the script is an asset_id or filename referenced
    in the prompt's asset list (see pipeline/script_gen.py); resolve it
    against the profile's actual media assets."""
    for asset in profile.media.assets:
        filename = asset.url.rsplit("/", 1)[-1]
        if visual_source in (asset.asset_id, filename):
            local_path = config.ASSETS_DIR / profile.pet_id / filename
            if not local_path.exists():
                raise FileNotFoundError(
                    f"Asset file not found: {local_path} "
                    f"(place uploaded media under storage/assets/{profile.pet_id}/)"
                )
            return local_path
    raise ValueError(
        f"Script referenced unknown visual_source {visual_source!r} not in profile media assets"
    )


def generate_video(
    *,
    profile_path: str,
    voice_sample: str | None = None,
    music_track: str | None = None,
    style: str = "cute",
    duration: int = 30,
) -> Path:
    """voice_sample=None runs without narration (silent placeholder audio
    per scene) — useful for testing script generation and video assembly
    before a TTS voice reference is available. music_track=None skips
    background music (narration/silence only)."""
    profile = PetProfile.load(profile_path)

    llm = OllamaLLMProvider()
    scripts = generate_all_styles(profile, llm, duration=duration)

    work_dir = config.OUTPUT_DIR / profile.pet_id
    scripts_dir = work_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name, s in scripts.items():
        missing = find_missing_disclosures(s, profile)
        s["_disclosure_check"] = {"missing_restrictions": missing}
        if missing:
            print(
                f"[WARNING] style={name!r} may be missing required disclosure(s): {missing} "
                "— review before this script is approved for publish."
            )
        (scripts_dir / f"{name}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    script = scripts[style]

    if voice_sample:
        tts = XTTSProvider()
        audio_paths = synthesize_scenes(
            script, tts, voice_profile=voice_sample, output_dir=work_dir / "audio"
        )
    else:
        audio_paths = silence_scenes(script, work_dir / "audio")

    video_clip_paths = []
    ordered_audio_paths = []
    for scene in script["scenes"]:
        visual_path = _resolve_visual_path(profile, scene["visual_source"])
        clip_path = work_dir / f"scene_{scene['scene_id']}.mp4"
        build_scene_clip(
            visual_path=str(visual_path),
            duration=scene["end"] - scene["start"],
            subtitle_text=scene["subtitle"],
            output_path=str(clip_path),
        )
        video_clip_paths.append(str(clip_path))
        ordered_audio_paths.append(audio_paths[scene["scene_id"]])

    total_duration = script["scenes"][-1]["end"]

    concatenated_video = concat_video_only(video_clip_paths, str(work_dir / "video_only.mp4"))
    concatenated_narration = concat_audio(ordered_audio_paths, str(work_dir / "narration_full.wav"))

    if music_track:
        final_audio = mix_narration_with_music(
            narration_path=concatenated_narration,
            music_path=music_track,
            duration=total_duration,
            output_path=str(work_dir / "audio_mixed.wav"),
        )
    else:
        final_audio = concatenated_narration

    final_path = work_dir / f"{profile.pet_id}_{style}_{duration}s.mp4"
    mux_video_audio(concatenated_video, final_audio, str(final_path))
    return final_path


def main():
    parser = argparse.ArgumentParser(
        description="PoC pipeline: generate a pet adoption video from a Pet Profile JSON"
    )
    parser.add_argument("--profile", required=True, help="Path to pet profile JSON")
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
    args = parser.parse_args()

    output_path = generate_video(
        profile_path=args.profile,
        voice_sample=args.voice_sample,
        music_track=args.music_track,
        style=args.style,
        duration=args.duration,
    )
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline import config
from pipeline.editing import build_scene_clip, concat_clips
from pipeline.narration import synthesize_scenes
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
    voice_sample: str,
    style: str = "cute",
    duration: int = 30,
) -> Path:
    profile = PetProfile.load(profile_path)

    llm = OllamaLLMProvider()
    scripts = generate_all_styles(profile, llm, duration=duration)

    work_dir = config.OUTPUT_DIR / profile.pet_id
    scripts_dir = work_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name, s in scripts.items():
        (scripts_dir / f"{name}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    script = scripts[style]

    tts = XTTSProvider()
    audio_paths = synthesize_scenes(
        script, tts, voice_profile=voice_sample, output_dir=work_dir / "audio"
    )

    clip_paths = []
    for scene in script["scenes"]:
        visual_path = _resolve_visual_path(profile, scene["visual_source"])
        clip_path = work_dir / f"scene_{scene['scene_id']}.mp4"
        build_scene_clip(
            visual_path=str(visual_path),
            duration=scene["end"] - scene["start"],
            subtitle_text=scene["subtitle"],
            narration_path=audio_paths[scene["scene_id"]],
            output_path=str(clip_path),
        )
        clip_paths.append(str(clip_path))

    final_path = work_dir / f"{profile.pet_id}_{style}_{duration}s.mp4"
    concat_clips(clip_paths, str(final_path))
    return final_path


def main():
    parser = argparse.ArgumentParser(
        description="PoC pipeline: generate a pet adoption video from a Pet Profile JSON"
    )
    parser.add_argument("--profile", required=True, help="Path to pet profile JSON")
    parser.add_argument("--voice-sample", required=True, help="Reference wav for TTS voice cloning")
    parser.add_argument("--style", default="cute", choices=SCRIPT_STYLES)
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    output_path = generate_video(
        profile_path=args.profile,
        voice_sample=args.voice_sample,
        style=args.style,
        duration=args.duration,
    )
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

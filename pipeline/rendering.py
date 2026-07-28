from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.audio_mix import mix_narration_with_music
from pipeline.editing import (
    build_scene_clip,
    concat_audio,
    concat_video_only,
    mux_video_audio,
)
from pipeline.narration import silence_scenes, synthesize_scenes
from pipeline.profile import PetProfile
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


def render_script(
    profile: PetProfile,
    script: dict,
    work_dir: Path,
    *,
    voice_sample: str | None = None,
    music_track: str | None = None,
) -> Path:
    """Render a single already-selected script into a final MP4 inside
    work_dir: narration/silence per scene, per-scene video clips (real
    footage or photo Ken Burns), concatenation, optional music mixing, and
    the final mux. Shared by pipeline.run.generate_video (fresh generation)
    and pipeline.regen.regenerate_scene (single-shot revision) so both
    paths render identically without duplicating this logic."""
    work_dir.mkdir(parents=True, exist_ok=True)

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

    # Sum actual per-scene clip durations rather than trusting scenes[-1]["end"]:
    # if the LLM's timeline has gaps/overlaps (see pipeline/qa.py), the
    # concatenated video's real length matches this sum, not the declared end time.
    total_duration = sum(scene["end"] - scene["start"] for scene in script["scenes"])

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

    final_path = work_dir / f"{profile.pet_id}_{script.get('style', 'video')}_{total_duration}s.mp4"
    mux_video_audio(concatenated_video, final_audio, str(final_path))
    return final_path

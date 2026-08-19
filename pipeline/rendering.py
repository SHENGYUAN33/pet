from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.audio_mix import mix_narration_with_music
from pipeline.editing import (
    PHOTO_EXTENSIONS,
    build_scene_clip,
    concat_audio,
    concat_video_only,
    mux_video_audio,
)
from pipeline.i2v import animate_photo, get_video_provider
from pipeline.narration import silence_scenes, synthesize_scenes
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback, noop
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
    animate_scenes: set[int] | None = None,
    video_provider: str = "svd",
    animate_prompt: str | None = None,
    on_progress: ProgressCallback = noop,
) -> Path:
    """Render a single already-selected script into a final MP4 inside
    work_dir: narration/silence per scene, per-scene video clips (real
    footage, photo Ken Burns, or Image-to-Video), concatenation, optional
    music mixing, and the final mux. Shared by pipeline.run.generate_video
    (fresh generation) and pipeline.regen.regenerate_scene (single-shot
    revision) so both paths render identically without duplicating this
    logic.

    animate_scenes (docs/architecture.md §5 strategy B): scene_ids whose
    photo source should be animated via an open-source Image-to-Video model
    instead of Ken Burns — only meaningful for photo-sourced scenes, and
    only instantiates the (heavy) video_provider once, not per scene.
    animate_prompt is optional motion guidance passed to every animated
    scene for prompt-conditioned providers (CogVideoX, Wan); ignored by
    providers that aren't text-conditioned (SVD). A single shared prompt
    rather than a per-scene mapping, since callers only ever animate one
    scene at a time today (pipeline.regen.regenerate_scene).

    on_progress reports which stage is running (see pipeline/progress.py);
    the CLI leaves it at the no-op default, the web UI uses it to drive a
    progress bar instead of a blank spinner."""
    work_dir.mkdir(parents=True, exist_ok=True)
    animate_scenes = animate_scenes or set()

    if voice_sample:
        on_progress("產生旁白配音（TTS）", 0.0)
        tts = XTTSProvider()
        audio_paths = synthesize_scenes(
            script, tts, voice_profile=voice_sample, output_dir=work_dir / "audio"
        )
    else:
        audio_paths = silence_scenes(script, work_dir / "audio")

    if animate_scenes:
        on_progress(f"載入 {video_provider} 影片生成模型", 0.15)
    i2v_provider = get_video_provider(video_provider) if animate_scenes else None

    video_clip_paths = []
    ordered_audio_paths = []
    scene_count = len(script["scenes"])
    # Scene rendering owns 0.2-0.9 of this stage; the surrounding TTS and
    # concat/mux steps are comparatively quick.
    for index, scene in enumerate(script["scenes"]):
        visual_path = _resolve_visual_path(profile, scene["visual_source"])
        duration = scene["end"] - scene["start"]

        scene_fraction = 0.2 + 0.7 * (index / scene_count)
        animated = (
            scene["scene_id"] in animate_scenes and visual_path.suffix.lower() in PHOTO_EXTENSIONS
        )
        on_progress(
            f"鏡頭 {index + 1}/{scene_count}"
            + (f"：{video_provider} 動態化中（比較久）" if animated else "：剪輯與字幕"),
            scene_fraction,
        )

        if animated:
            i2v_path = work_dir / f"scene_{scene['scene_id']}_i2v.mp4"
            animate_photo(
                str(visual_path),
                i2v_provider,
                duration=duration,
                output_path=str(i2v_path),
                prompt=animate_prompt,
            )
            # build_scene_clip below treats any non-photo-suffix input as
            # real footage (loop-if-short + crop, see pipeline/editing.py),
            # which is exactly what a raw I2V clip needs too.
            visual_path = i2v_path

        clip_path = work_dir / f"scene_{scene['scene_id']}.mp4"
        build_scene_clip(
            visual_path=str(visual_path),
            duration=duration,
            subtitle_text=scene["subtitle"],
            output_path=str(clip_path),
        )
        video_clip_paths.append(str(clip_path))
        ordered_audio_paths.append(audio_paths[scene["scene_id"]])

    # Sum actual per-scene clip durations rather than trusting scenes[-1]["end"]:
    # if the LLM's timeline has gaps/overlaps (see pipeline/qa.py), the
    # concatenated video's real length matches this sum, not the declared end time.
    total_duration = sum(scene["end"] - scene["start"] for scene in script["scenes"])

    on_progress("合併鏡頭與音訊", 0.9)
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
    on_progress("影片輸出完成", 1.0)
    return final_path

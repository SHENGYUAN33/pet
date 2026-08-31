from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.audio_mix import mix_narration_with_music
from pipeline.background import (
    BackgroundMode,
    SceneBackground,
    apply_background,
    get_image_provider,
    resolve_scene_background,
)
from pipeline.editing import (
    PHOTO_EXTENSIONS,
    build_recap_clip,
    build_scene_clip,
    concat_audio,
    concat_video_only,
    mux_video_audio,
)
from pipeline.i2v import animate_photo, get_video_provider
from pipeline.montage import scene_sources
from pipeline.narration import silence_scenes, synthesize_scenes
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback, noop
from pipeline.scene_tracking import NoopSceneTracker, SceneTracker
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


def _resolve_scene_visuals(profile: PetProfile, scene: dict) -> list[Path]:
    """Every asset path a scene shows, in order. Story scenes name one; the
    closing recap (pipeline/montage.py) names several."""
    return [_resolve_visual_path(profile, source) for source in scene_sources(scene)]


def _is_single_photo(visual_paths: list[Path]) -> bool:
    """Whether a scene is one still photograph.

    Both generative steps need that. I2V animates a single still, and
    outpainting extends one — the closing recap shows several assets in one
    shot, and real footage already moves and already fills the frame, so
    neither is a candidate for either. A scene id naming one is simply left
    alone rather than treated as an error: which scenes a video ends up with
    is the script's decision, not the caller's.
    """
    return len(visual_paths) == 1 and visual_paths[0].suffix.lower() in PHOTO_EXTENSIONS


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
    background_scenes: set[int] | None = None,
    background_mode: BackgroundMode = BackgroundMode.EXTEND,
    image_provider: str = "comfy",
    background_prompt: str | None = None,
    on_progress: ProgressCallback = noop,
    scene_tracker: SceneTracker | None = None,
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

    Backgrounds (pipeline/background.py) come from the script: each scene
    may carry a `background` block saying whether to keep the photograph as
    it is, fill in the empty frame margin (EXTEND), or replace the setting
    entirely (REPLACE), with its own description. That is what lets a video
    move through places — a cage, then a street, then a home — rather than
    repeating one setting six times. The script's `art_direction` is added
    to every one of those descriptions, which is what keeps the shots
    looking like one film.

    background_scenes/background_mode/background_prompt override the script
    for the scene_ids named, and are what a reviewer correcting one shot
    uses. Only meaningful for photo-sourced scenes either way, and applied
    *before* animation, so a scene doing both is animated from the finished
    picture rather than from the original. REPLACE additionally burns the
    AI-generation disclosure into those shots (docs/architecture.md §5
    strategy C) — an invented setting has to be visible as one, and doing it
    here rather than leaving it to the caller means it cannot be forgotten.

    on_progress reports which stage is running (see pipeline/progress.py);
    the CLI leaves it at the no-op default, the web UI uses it to drive a
    progress bar instead of a blank spinner.

    scene_tracker (see pipeline/scene_tracking.py) records each scene's
    outcome and can hand back a clip an earlier attempt already finished, so
    a resumed run skips it. Default is the no-op tracker: render every
    scene, record nothing — which is what a caller without a job row wants.
    Passed in rather than looked up here so this module stays free of any
    database dependency."""
    work_dir.mkdir(parents=True, exist_ok=True)
    animate_scenes = animate_scenes or set()
    tracker = scene_tracker or NoopSceneTracker()

    # Settled once, here, so the preflight below and the render loop cannot
    # disagree about which shots are getting a generated background.
    backgrounds = {
        scene["scene_id"]: resolve_scene_background(
            scene,
            art_direction=script.get("art_direction"),
            override_scenes=background_scenes,
            override_mode=background_mode,
            override_prompt=background_prompt,
        )
        for scene in script["scenes"]
    }

    # Which scenes a resumed run can skip, decided once so the check below and
    # the render loop agree on it.
    reusable_clips = {
        scene["scene_id"]: tracker.reusable_clip(scene["scene_id"]) for scene in script["scenes"]
    }
    # Resolve every source that still has to be rendered before the TTS pass:
    # a script naming an asset the profile doesn't have is unrenderable, and
    # discovering that at scene 1 wastes a full narration pass first. Scenes
    # whose clip is being reused aren't checked — their source may legitimately
    # have been removed since that clip was produced.
    will_animate = False
    background_modes: set[BackgroundMode] = set()
    for scene in script["scenes"]:
        if reusable_clips[scene["scene_id"]] is not None:
            continue
        visual_paths = _resolve_scene_visuals(profile, scene)
        if not _is_single_photo(visual_paths):
            continue
        if scene["scene_id"] in animate_scenes:
            will_animate = True
        background = backgrounds[scene["scene_id"]]
        if background is not None:
            background_modes.add(background.mode)

    # Same reasoning one step further out: if any scene still needs I2V, make
    # sure the provider can actually be reached before the narration pass,
    # rather than after it (a stopped ComfyUI server is the common case).
    # The instance is thrown away — constructing one is cheap, model weights
    # only load on the first animate_image call — so the lazy load below
    # still decides when the expensive part happens.
    if will_animate:
        on_progress(f"檢查 {video_provider} 影片生成服務", 0.0)
        get_video_provider(video_provider).preflight()
    if background_modes:
        on_progress(f"檢查 {image_provider} 背景生成服務", 0.0)
        provider = get_image_provider(image_provider)
        # Checked per mode actually used, because the two treatments need
        # different models installed: demanding the matting weights for a run
        # that only fills margins would refuse work that would have succeeded.
        for mode in sorted(background_modes, key=lambda m: m.value):
            provider.preflight(mode=mode.value)

    if voice_sample:
        on_progress("產生旁白配音（TTS）", 0.0)
        tts = XTTSProvider()
        audio_paths = synthesize_scenes(
            script, tts, voice_profile=voice_sample, output_dir=work_dir / "audio"
        )
    else:
        audio_paths = silence_scenes(script, work_dir / "audio")

    # Loaded on first use rather than up front: the model costs minutes to
    # load, and a resumed run whose animated scenes are already done never
    # needs it at all.
    i2v_provider = None
    background_provider = None

    def load_i2v_provider():
        nonlocal i2v_provider
        if i2v_provider is None:
            on_progress(f"載入 {video_provider} 影片生成模型", 0.15)
            i2v_provider = get_video_provider(video_provider)
        return i2v_provider

    def load_background_provider():
        nonlocal background_provider
        if background_provider is None:
            background_provider = get_image_provider(image_provider)
        return background_provider

    def disclosure_for(background: SceneBackground | None) -> str | None:
        """The AI-generation label a shot has to carry, or None.

        Only a replaced setting earns it: with EXTEND nothing the camera saw
        is replaced, and labelling a filled-in margin would wear the label
        out where it actually matters.
        """
        if background is not None and background.mode is BackgroundMode.REPLACE:
            return config.BACKGROUND_DISCLOSURE_TEXT
        return None

    video_clip_paths = []
    ordered_audio_paths = []
    scene_count = len(script["scenes"])
    # Scene rendering owns 0.2-0.9 of this stage; the surrounding TTS and
    # concat/mux steps are comparatively quick.
    for index, scene in enumerate(script["scenes"]):
        scene_id = scene["scene_id"]
        duration = scene["end"] - scene["start"]
        scene_fraction = 0.2 + 0.7 * (index / scene_count)
        ordered_audio_paths.append(audio_paths[scene_id])

        reused = reusable_clips[scene_id]
        if reused is not None:
            on_progress(f"鏡頭 {index + 1}/{scene_count}：沿用上次已完成的結果", scene_fraction)
            video_clip_paths.append(reused)
            continue

        visual_paths = _resolve_scene_visuals(profile, scene)
        single_photo = _is_single_photo(visual_paths)
        animated = scene_id in animate_scenes and single_photo
        background = backgrounds[scene_id] if single_photo else None
        # Named after the step that runs *first*, since a scene doing both
        # spends its opening minutes on the background; the animation step
        # re-reports itself when it takes over.
        if background is not None:
            step = f"：{image_provider} {background.mode.label}背景生成中"
        elif animated:
            step = f"：{video_provider} 動態化中（比較久）"
        else:
            step = "：剪輯與字幕"
        on_progress(f"鏡頭 {index + 1}/{scene_count}{step}", scene_fraction)

        tracker.start_scene(
            scene_id,
            visual_source=", ".join(scene_sources(scene)),
            video_provider=video_provider if animated else None,
            animate_prompt=animate_prompt if animated else None,
            image_provider=image_provider if background else None,
            background_mode=background.mode.value if background else None,
            background_prompt=background.prompt if background else None,
        )
        clip_path = work_dir / f"scene_{scene_id}.mp4"
        try:
            if len(visual_paths) > 1:
                # The closing recap: several assets sharing one shot.
                build_recap_clip(
                    visual_paths=[str(p) for p in visual_paths],
                    duration=duration,
                    subtitle_text=scene["subtitle"],
                    output_path=str(clip_path),
                )
            else:
                visual_path = visual_paths[0]
                if background is not None:
                    # Cached under work_dir rather than regenerated: a resumed
                    # run that died during this scene's animation should not
                    # pay for the background a second time, and reusing the
                    # exact file also keeps the retry visually identical to
                    # the attempt it continues.
                    bg_path = work_dir / f"scene_{scene_id}_bg.png"
                    if bg_path.exists():
                        visual_path = bg_path
                    else:
                        visual_path = Path(
                            apply_background(
                                str(visual_path),
                                load_background_provider(),
                                mode=background.mode,
                                output_path=str(bg_path),
                                prompt=background.prompt,
                                # The profile knows what animal this is and
                                # the provider does not; REPLACE has to be
                                # told what to keep in the frame.
                                subject=profile.species,
                            )
                        )
                    if animated:
                        on_progress(
                            f"鏡頭 {index + 1}/{scene_count}：{video_provider} 動態化中（比較久）",
                            scene_fraction,
                        )

                if animated:
                    i2v_path = work_dir / f"scene_{scene_id}_i2v.mp4"
                    animate_photo(
                        str(visual_path),
                        load_i2v_provider(),
                        duration=duration,
                        output_path=str(i2v_path),
                        prompt=animate_prompt,
                    )
                    # build_scene_clip below treats any non-photo-suffix input
                    # as real footage (loop-if-short + crop, see
                    # pipeline/editing.py), which is what a raw I2V clip needs.
                    visual_path = i2v_path

                build_scene_clip(
                    visual_path=str(visual_path),
                    duration=duration,
                    subtitle_text=scene["subtitle"],
                    output_path=str(clip_path),
                    disclosure_text=disclosure_for(background),
                )
        except Exception as e:
            # Boundary: FFmpeg and the I2V providers are external. Record
            # which scene died before re-raising, so a resume knows where to
            # pick up and the reviewer sees which shot was the problem.
            tracker.fail_scene(scene_id, f"{type(e).__name__}: {e}")
            raise

        tracker.finish_scene(scene_id, str(clip_path))
        video_clip_paths.append(str(clip_path))

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

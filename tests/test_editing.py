from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline import config
from pipeline.audio_mix import mix_narration_with_music
from pipeline.editing import (
    FRAME_HEIGHT,
    FRAME_RATE,
    FRAME_WIDTH,
    PHOTO_SUPERSAMPLE,
    _fit_to_frame,
    build_scene_clip,
    concat_audio,
    concat_video_only,
    mux_video_audio,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

DURATION_TOLERANCE = 0.2


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _probe(path, entries: str) -> str:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            entries,
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _duration(path) -> float:
    return float(_probe(path, "format=duration"))


@pytest.fixture
def sample_photo(tmp_path):
    photo = tmp_path / "photo.jpg"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=640x480:d=1",
            "-frames:v",
            "1",
            str(photo),
        ]
    )
    return photo


@pytest.fixture
def sample_video(tmp_path):
    """A 2-second clip — shorter than most scene durations, to exercise the
    stream_loop fallback for real footage shorter than its assigned scene."""
    video = tmp_path / "clip.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:duration=2:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    return video


def test_build_scene_clip_from_photo_has_target_duration_and_frame_size(sample_photo, tmp_path):
    out = tmp_path / "scene.mp4"
    build_scene_clip(
        visual_path=str(sample_photo), duration=3.0, subtitle_text="測試字幕", output_path=str(out)
    )

    assert abs(_duration(out) - 3.0) < DURATION_TOLERANCE
    width, height = _probe(out, "stream=width,height").splitlines()
    assert (int(width), int(height)) == (FRAME_WIDTH, FRAME_HEIGHT)


def test_build_scene_clip_from_short_video_loops_to_fill_duration(sample_video, tmp_path):
    """Regression test: real clips are often shorter than their assigned
    scene (source is 2s here); the output must still reach the requested
    duration via -stream_loop rather than ending early."""
    out = tmp_path / "scene.mp4"
    build_scene_clip(
        visual_path=str(sample_video), duration=5.0, subtitle_text="測試", output_path=str(out)
    )

    assert abs(_duration(out) - 5.0) < DURATION_TOLERANCE


def test_photo_and_video_scenes_share_constant_frame_rate(sample_photo, sample_video, tmp_path):
    """Regression test for the bug where mismatched fps between photo
    (Ken Burns) and real-video scenes corrupted concat timestamps and
    silently truncated the final duration."""
    photo_clip = tmp_path / "p.mp4"
    video_clip = tmp_path / "v.mp4"
    build_scene_clip(
        visual_path=str(sample_photo), duration=2.0, subtitle_text="a", output_path=str(photo_clip)
    )
    build_scene_clip(
        visual_path=str(sample_video), duration=2.0, subtitle_text="b", output_path=str(video_clip)
    )

    for clip in (photo_clip, video_clip):
        assert _probe(clip, "stream=r_frame_rate") == f"{FRAME_RATE}/1"


def test_concat_video_only_preserves_total_duration_across_mixed_sources(
    sample_photo, sample_video, tmp_path
):
    """Regression test for the exact bug found during real-asset testing:
    concatenating a photo scene + a video scene came out shorter than the
    sum of their durations before the constant-fps fix."""
    clip1 = tmp_path / "c1.mp4"
    clip2 = tmp_path / "c2.mp4"
    build_scene_clip(
        visual_path=str(sample_photo), duration=2.0, subtitle_text="a", output_path=str(clip1)
    )
    build_scene_clip(
        visual_path=str(sample_video), duration=3.0, subtitle_text="b", output_path=str(clip2)
    )

    out = tmp_path / "concat.mp4"
    concat_video_only([str(clip1), str(clip2)], str(out))

    assert abs(_duration(out) - 5.0) < DURATION_TOLERANCE


def test_concat_audio_preserves_total_duration(tmp_path):
    a1, a2 = tmp_path / "a1.wav", tmp_path / "a2.wav"
    for path, dur in ((a1, 1.5), (a2, 2.5)):
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                str(dur),
                str(path),
            ]
        )

    out = tmp_path / "combined.wav"
    concat_audio([str(a1), str(a2)], str(out))

    assert abs(_duration(out) - 4.0) < 0.1


def test_mux_video_audio_produces_both_streams(sample_photo, tmp_path):
    video_clip = tmp_path / "v.mp4"
    build_scene_clip(
        visual_path=str(sample_photo), duration=2.0, subtitle_text="x", output_path=str(video_clip)
    )

    audio = tmp_path / "a.wav"
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2", str(audio)])

    out = tmp_path / "final.mp4"
    mux_video_audio(str(video_clip), str(audio), str(out))

    codec_types = _probe(out, "stream=codec_type").splitlines()
    assert "video" in codec_types
    assert "audio" in codec_types
    assert abs(_duration(out) - 2.0) < DURATION_TOLERANCE


def test_mix_narration_with_music_matches_narration_duration(tmp_path):
    narration = tmp_path / "narration.wav"
    music = tmp_path / "music.wav"
    _run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "6", str(narration)]
    )
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(music)])

    out = tmp_path / "mixed.wav"
    mix_narration_with_music(
        narration_path=str(narration), music_path=str(music), duration=6.0, output_path=str(out)
    )

    assert abs(_duration(out) - 6.0) < DURATION_TOLERANCE


def test_build_scene_clip_survives_filtergraph_metacharacters_in_subtitle(sample_photo, tmp_path):
    """Regression test: subtitles are LLM-written and reviewer-editable, so
    they routinely contain characters that used to break the filtergraph —
    an apostrophe closed drawtext's quoted text early and the following comma
    was then read as a filter separator ("No such filter"), failing the whole
    render. Nothing here may need escaping by the caller."""
    out = tmp_path / "scene.mp4"

    build_scene_clip(
        visual_path=str(sample_photo),
        duration=2.0,
        subtitle_text=r"Meow! I'm 元寶, a playful kitty: 100% 好動 [test]; \o/",
        output_path=str(out),
    )

    assert abs(_duration(out) - 2.0) < DURATION_TOLERANCE


def _landscape_photo_with_marked_edges(tmp_path):
    """A 4:3 source whose left and right edges are solid red and blue.

    Those edges are exactly what filling a 9:16 frame by cropping throws
    away, so their presence in the output is the check that the picture
    survived whole.
    """
    photo = tmp_path / "landscape.png"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=1600x1200:d=1",
            "-vf",
            (
                "drawbox=x=0:y=0:w=200:h=1200:color=red@1:t=fill,"
                "drawbox=x=1400:y=0:w=200:h=1200:color=blue@1:t=fill"
            ),
            "-frames:v",
            "1",
            str(photo),
        ]
    )
    return photo


def _edge_colors(path) -> tuple[bytes, bytes]:
    """(left, right) colour at the vertical middle of the frame, each
    averaged to a single RGB pixel."""
    colors = []
    for x in (0, FRAME_WIDTH - 20):
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                f"crop=20:40:{x}:{FRAME_HEIGHT // 2},scale=1:1,format=rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        colors.append(result.stdout[:3])
    return colors[0], colors[1]


@pytest.mark.parametrize("mode", ["blur", "pad"])
def test_a_landscape_source_keeps_the_edges_cropping_would_cut(tmp_path, monkeypatch, mode):
    """A 4:3 photo has to reach 2640px wide to cover a 1080x1920 frame, so
    filling by cropping keeps 41% of it — usually losing half the pet. Both
    fitting modes put the whole picture in the frame, so its outer edges are
    still there."""
    photo = _landscape_photo_with_marked_edges(tmp_path)

    monkeypatch.setattr(config, "SCENE_FIT_MODE", mode)
    fitted = tmp_path / f"{mode}.mp4"
    build_scene_clip(
        visual_path=str(photo), duration=1.0, subtitle_text="測試", output_path=str(fitted)
    )

    assert _probe(fitted, "stream=width,height").split() == [str(FRAME_WIDTH), str(FRAME_HEIGHT)]

    left, right = _edge_colors(fitted)
    assert left[0] > left[2], f"left edge should still be the red band, got {tuple(left)}"
    assert right[2] > right[0], f"right edge should still be the blue band, got {tuple(right)}"

    monkeypatch.setattr(config, "SCENE_FIT_MODE", "crop")
    cropped = tmp_path / "cropped.mp4"
    build_scene_clip(
        visual_path=str(photo), duration=1.0, subtitle_text="測試", output_path=str(cropped)
    )

    crop_left, crop_right = _edge_colors(cropped)
    assert crop_left[0] <= crop_left[2] and crop_right[2] <= crop_right[0], (
        "cropping to fill should have cut both coloured edges away — "
        "if it didn't, this test no longer proves anything"
    )


def test_fit_modes_produce_the_filter_each_one_promises():
    """The three modes differ in exactly one thing: what happens to the space
    a non-9:16 source doesn't cover."""
    with_blur = _fit_to_frame()
    assert "gblur" in with_blur and "overlay" in with_blur

    import pipeline.config as cfg

    original = cfg.SCENE_FIT_MODE
    try:
        cfg.SCENE_FIT_MODE = "pad"
        assert "pad=" in _fit_to_frame()
        assert "gblur" not in _fit_to_frame()

        cfg.SCENE_FIT_MODE = "crop"
        cropping = _fit_to_frame()
        assert "crop=" in cropping
        assert "overlay" not in cropping
    finally:
        cfg.SCENE_FIT_MODE = original


def test_ken_burns_samples_from_an_oversized_frame():
    """Zooming into a picture already at output resolution is what makes a
    photo scene look soft."""
    assert PHOTO_SUPERSAMPLE > 1
    oversized = _fit_to_frame(PHOTO_SUPERSAMPLE)
    assert str(FRAME_WIDTH * PHOTO_SUPERSAMPLE) in oversized


def test_a_frame_shaped_source_skips_the_blurred_backdrop(tmp_path):
    """A generated background comes out at exactly the output ratio, so the
    backdrop chain would blur an 8-megapixel frame and then cover every pixel
    of it. FFmpeg was intermittently failing on that chain, on precisely the
    shots where it was doing nothing."""
    from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH, _fit_filter_for

    portrait = tmp_path / "portrait.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s={FRAME_WIDTH // 2}x{FRAME_HEIGHT // 2}:d=1",
            "-frames:v",
            "1",
            str(portrait),
        ],
        check=True,
        capture_output=True,
    )

    chain = _fit_filter_for(str(portrait))

    assert "gblur" not in chain
    assert "overlay" not in chain
    assert f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}" in chain


def test_a_differently_shaped_source_still_gets_the_backdrop(tmp_path):
    """The waste only exists when there is no leftover space; a landscape
    photo still needs something in the bands."""
    from pipeline.editing import _fit_filter_for

    landscape = tmp_path / "landscape.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=640x480:d=1",
            "-frames:v",
            "1",
            str(landscape),
        ],
        check=True,
        capture_output=True,
    )

    assert "gblur" in _fit_filter_for(str(landscape))


def test_an_unreadable_source_falls_back_to_the_general_chain(tmp_path):
    """Which of two correct chains to use is not worth losing a shot over."""
    from pipeline.editing import _fit_filter_for

    assert "gblur" in _fit_filter_for(str(tmp_path / "not-a-picture.png"))


def test_a_still_is_decoded_once_rather_than_per_frame(tmp_path):
    """`-loop 1` re-reads the file for every output frame — 150 times for a
    six-second shot — and FFmpeg was intermittently failing there: decoder
    errors on PNGs that decode cleanly on their own, and twice an access
    violation that killed a run. Repeating one decoded frame removes the
    mechanism instead of working around it."""
    from pipeline.editing import PHOTO_LOOP_FILTER

    photo = tmp_path / "photo.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=640x480:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "clip.mp4"

    recorded = {}
    real_run = subprocess.run

    def capture(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffmpeg":
            recorded.setdefault("cmd", cmd)
        return real_run(cmd, *args, **kwargs)

    from pipeline import editing

    editing.subprocess.run = capture
    try:
        build_scene_clip(
            visual_path=str(photo), duration=1.0, subtitle_text="字幕", output_path=str(out)
        )
    finally:
        editing.subprocess.run = real_run

    cmd = recorded["cmd"]
    assert "-loop" not in cmd
    assert PHOTO_LOOP_FILTER in " ".join(cmd)
    assert out.exists()


def test_a_long_subtitle_is_wrapped_rather_than_cut_off():
    """drawtext will not wrap: anything wider than the frame is cut off at
    both edges, silently. Measured on a real run — the script model wrote an
    English subtitle, sailed past the character rule it had been given for
    narration, and both ends of the sentence were missing from the video."""
    from pipeline.editing import _display_width, wrap_burned_text

    lines = wrap_burned_text("I love playing with wand toys! Meow meow meow~", 30).split("\n")

    assert len(lines) == 2
    assert all(_display_width(line) <= 30 for line in lines)


def test_wrapping_breaks_chinese_between_characters():
    """Chinese has no spaces to break at, and is set that way anyway."""
    from pipeline.editing import _display_width, wrap_burned_text

    lines = wrap_burned_text("我已經完成健康檢查，也會乖乖使用尿墊，快來領養我吧", 30).split("\n")

    assert len(lines) == 2
    assert all(_display_width(line) <= 30 for line in lines)
    assert "".join(lines) == "我已經完成健康檢查，也會乖乖使用尿墊，快來領養我吧"


def test_a_short_subtitle_is_left_on_one_line():
    from pipeline.editing import wrap_burned_text

    assert "\n" not in wrap_burned_text("最喜歡玩逗貓棒，喵喵喵～", 30)


def test_a_cjk_character_counts_as_two_latin_ones():
    """Counting characters would wrap Chinese far too late and English far
    too early, since a CJK glyph is about twice as wide at the same size."""
    from pipeline.editing import _display_width

    assert _display_width("貓") == 2
    assert _display_width("ab") == 2


def test_the_pets_details_drop_clear_of_the_ai_disclosure(tmp_path):
    """Both sit at the top of the frame; stacked without a gap they read as
    one cluttered block rather than two separate things."""
    photo = tmp_path / "photo.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=640x480:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )
    commands = []
    real_run = subprocess.run

    def capture(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffmpeg":
            commands.append(cmd)
        return real_run(cmd, *args, **kwargs)

    from pipeline import editing

    editing.subprocess.run = capture
    try:
        for name, disclosure in (("plain", None), ("labelled", "部分畫面由 AI 創意生成")):
            build_scene_clip(
                visual_path=str(photo),
                duration=0.2,
                subtitle_text="字幕",
                output_path=str(tmp_path / f"{name}.mp4"),
                accent_colour="0xFF8FA3",
                info_card_text="豆豆 · 2歲",
                disclosure_text=disclosure,
            )
    finally:
        editing.subprocess.run = real_run

    plain, labelled = (" ".join(cmd) for cmd in commands)
    assert f"y={config.DECOR_INFO_CARD_Y}:" in plain
    assert f"y={config.DECOR_INFO_CARD_Y + config.DECOR_DISCLOSURE_CLEARANCE}:" in labelled


def test_a_composed_overlay_is_burned_into_the_shot(sample_photo, tmp_path):
    """The Pillow-drawn panel has to survive the encode, and the clip has to
    stay exactly the delivery size — every clip is stream-copied into the
    finished video, so a shot that came out a different shape breaks concat.
    """
    from PIL import Image

    from pipeline.editing import FRAME_HEIGHT as H
    from pipeline.editing import FRAME_WIDTH as W

    overlay = tmp_path / "overlay.png"
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # A solid magenta block in the middle band, well clear of the subtitle
    # and of the grey source picture's own colour.
    panel.paste((255, 0, 255, 255), (200, 700, 880, 1100))
    panel.save(overlay)

    out = tmp_path / "scene.mp4"
    build_scene_clip(
        visual_path=str(sample_photo),
        duration=0.4,
        subtitle_text="字幕",
        output_path=str(out),
        overlay_path=overlay,
    )

    assert _probe(out, "stream=width,height").split() == [str(W), str(H)]

    frame = tmp_path / "frame.png"
    _run(["ffmpeg", "-y", "-i", str(out), "-frames:v", "1", str(frame)])
    red, green, blue = Image.open(frame).convert("RGB").getpixel((540, 900))
    assert red > 150 and blue > 150 and green < 100


def test_a_missing_overlay_file_leaves_the_shot_alone(sample_photo, tmp_path):
    """Nothing about a panel is worth failing a render over — the shot is
    made without it, the same way a dropped template is handled upstream."""
    out = tmp_path / "scene.mp4"
    build_scene_clip(
        visual_path=str(sample_photo),
        duration=0.4,
        subtitle_text="字幕",
        output_path=str(out),
        overlay_path=tmp_path / "never_drawn.png",
    )
    assert out.exists()


def _subtitle_line_bands(frame_path) -> list[tuple[int, int]]:
    """Top/bottom of each band of near-white ink in the subtitle's half of
    the frame — one band per rendered line."""
    from PIL import Image

    grey = Image.open(frame_path).convert("L")
    bands: list[list[int]] = []
    current = None
    for y in range(grey.height // 2, grey.height):
        ink = sum(grey.crop((0, y, grey.width, y + 1)).histogram()[231:])
        if ink > 2:
            current = [y, y] if current is None else [current[0], y]
        elif current:
            bands.append(current)
            current = None
    if current:
        bands.append(current)
    return [(top, bottom) for top, bottom in bands if bottom - top >= 8]


def test_a_wrapped_subtitle_reads_as_one_sentence_not_two_captions(sample_photo, tmp_path):
    """The font's own line height puts 144px between lines of 47px glyphs —
    a blank gap twice the height of the text, which reads as two unrelated
    captions. config.SUBTITLE_LINE_SPACING pulls it back to ordinary leading;
    this guards both ends, since overshooting collides the lines instead.
    """
    out = tmp_path / "scene.mp4"
    build_scene_clip(
        visual_path=str(sample_photo),
        duration=0.2,
        # Long enough to wrap at config.SUBTITLE_MAX_UNITS.
        subtitle_text="牠已經等了 143 天，希望下一個是你",
        output_path=str(out),
    )
    frame = tmp_path / "frame.png"
    _run(["ffmpeg", "-y", "-i", str(out), "-frames:v", "1", str(frame)])

    bands = _subtitle_line_bands(frame)
    assert len(bands) >= 2, "subtitle did not wrap — the test text is no longer long enough"

    glyph_height = bands[0][1] - bands[0][0]
    blank_gap = bands[1][0] - bands[0][1]
    # Not touching, and not adrift: a gap between roughly a third and a whole
    # line of text. Unset, this measured ~100px against 47px glyphs.
    assert 0 < blank_gap < glyph_height, f"line gap {blank_gap}px vs {glyph_height}px glyphs"

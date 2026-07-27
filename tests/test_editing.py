from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline.audio_mix import mix_narration_with_music
from pipeline.editing import (
    FRAME_HEIGHT,
    FRAME_RATE,
    FRAME_WIDTH,
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

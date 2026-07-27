from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline import config

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Output frame: 9:16 vertical, matching docs/architecture.md platform target
FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920

# All scene clips must share this exact constant frame rate: concat_video_only
# stream-copies clips together, and mismatched fps/timebase between a photo
# (Ken Burns, generated at a fixed fps) and a real video clip (native fps)
# corrupts the concatenated timestamps, silently truncating the final duration.
FRAME_RATE = 25


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_filter_path(path: str) -> str:
    """Escape a filesystem path for embedding inside an ffmpeg filter
    argument (e.g. drawtext=fontfile=...). Forward slashes avoid Windows
    backslash-escaping headaches; the drive-letter colon still needs escaping."""
    return path.replace("\\", "/").replace(":", "\\:")


def build_scene_clip(
    *,
    visual_path: str,
    duration: float,
    subtitle_text: str,
    output_path: str,
) -> str:
    """Render one scene's video (no audio): real video or photo (Ken Burns
    zoom) with burned subtitle. Real-footage-first per
    docs/architecture.md §5 strategy A — no AI video generation in PoC.
    Audio (narration + music) is assembled separately and muxed in at the
    end — see concat_video_only / mux_video_audio and pipeline/audio_mix.py."""
    is_photo = Path(visual_path).suffix.lower() in PHOTO_EXTENSIONS

    if is_photo:
        frames = max(int(duration * FRAME_RATE), 1)
        video_filter = (
            f"scale={FRAME_WIDTH * 2}:-1,"
            f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}:s={FRAME_WIDTH}x{FRAME_HEIGHT}:fps={FRAME_RATE},"
        )
        video_input = ["-loop", "1", "-i", visual_path]
    else:
        video_filter = (
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={FRAME_WIDTH}:{FRAME_HEIGHT},"
            f"fps={FRAME_RATE},"
        )
        # Real clips are often shorter than the scene's assigned duration;
        # loop so -t below can always fill the full scene length.
        video_input = ["-stream_loop", "-1", "-i", visual_path]

    drawtext = (
        f"drawtext=fontfile='{_escape_filter_path(config.DRAWTEXT_FONT_FILE)}':"
        f"text='{_escape_drawtext(subtitle_text)}':"
        "fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=12:"
        "x=(w-text_w)/2:y=h-260"
    )

    cmd = [
        "ffmpeg",
        "-y",
        *video_input,
        "-t",
        str(duration),
        "-vf",
        f"{video_filter}{drawtext}",
        "-an",
        "-r",
        str(FRAME_RATE),
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path


def _concat_via_demuxer(paths: list[str], output_path: str, *, extra_args: list[str]) -> str:
    list_file = Path(output_path).with_suffix(".concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        f.writelines(f"file '{Path(p).resolve().as_posix()}'\n" for p in paths)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        *extra_args,
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path


def concat_video_only(clip_paths: list[str], output_path: str) -> str:
    """Concatenate the (audio-less) per-scene video clips into one track."""
    return _concat_via_demuxer(clip_paths, output_path, extra_args=["-c:v", "copy"])


def concat_audio(audio_paths: list[str], output_path: str) -> str:
    """Concatenate per-scene narration/silence wavs into one continuous
    narration track, in scene order."""
    return _concat_via_demuxer(audio_paths, output_path, extra_args=["-c:a", "copy"])


def mux_video_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """Combine the concatenated video track with the final mixed audio
    track (narration + music, see pipeline/audio_mix.py) into the deliverable MP4."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path

from __future__ import annotations

import subprocess
from pathlib import Path

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Output frame: 9:16 vertical, matching docs/architecture.md platform target
FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def build_scene_clip(
    *,
    visual_path: str,
    duration: float,
    subtitle_text: str,
    narration_path: str,
    output_path: str,
) -> str:
    """Render one scene: real video or photo (Ken Burns zoom), burned
    subtitle, narration audio track. Real-footage-first per
    docs/architecture.md §5 strategy A — no AI video generation in PoC."""
    is_photo = Path(visual_path).suffix.lower() in PHOTO_EXTENSIONS

    if is_photo:
        frames = max(int(duration * 25), 1)
        video_filter = (
            f"scale={FRAME_WIDTH * 2}:-1,"
            f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}:s={FRAME_WIDTH}x{FRAME_HEIGHT}:fps=25,"
        )
        video_input = ["-loop", "1", "-i", visual_path]
    else:
        video_filter = (
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={FRAME_WIDTH}:{FRAME_HEIGHT},"
        )
        video_input = ["-i", visual_path]

    drawtext = (
        f"drawtext=text='{_escape_drawtext(subtitle_text)}':"
        "fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=12:"
        "x=(w-text_w)/2:y=h-260"
    )

    cmd = [
        "ffmpeg", "-y",
        *video_input,
        "-i", narration_path,
        "-t", str(duration),
        "-vf", f"{video_filter}{drawtext}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path


def concat_clips(clip_paths: list[str], output_path: str) -> str:
    list_file = Path(output_path).with_suffix(".txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{Path(p).resolve().as_posix()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path

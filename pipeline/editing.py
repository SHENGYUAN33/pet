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

# The Ken Burns move zooms into the fitted frame, so that frame is built at
# this multiple of the output size — zooming into a 1080x1920 picture that is
# already at output resolution is what makes a photo scene look soft.
PHOTO_SUPERSAMPLE = 2


def _write_text_file(text: str, output_path: str, *, suffix: str) -> Path:
    """Put burned-in text in a sidecar file for drawtext's textfile= option
    instead of inlining it in the filtergraph.

    Inlining meant escaping the same string for three nested parsers
    (filtergraph, filter options, drawtext), and getting it wrong broke the
    whole render: an apostrophe closed the quoted section early, after which
    the next comma in an LLM-written subtitle ("I'm Yuanbao, a playful
    kitty!") read as a filter separator and FFmpeg failed with "No such
    filter". With textfile= only the path is part of the filter string, and
    subtitle text — which is model-generated and reviewer-editable, i.e.
    never under our control — needs no escaping at all. The same applies to
    the AI-generation disclosure, which is reviewer-configurable."""
    path = Path(output_path).with_suffix(suffix)
    path.write_text(text, encoding="utf-8")
    return path


def _escape_filter_path(path: str) -> str:
    """Escape a filesystem path for embedding inside an ffmpeg filter
    argument (e.g. drawtext=fontfile=...). Forward slashes avoid Windows
    backslash-escaping headaches; the drive-letter colon still needs escaping."""
    return path.replace("\\", "/").replace(":", "\\:")


#: Repeat one decoded frame instead of re-reading the file per output frame.
#:
#: The obvious way to hold a still on screen is `-loop 1`, which re-decodes
#: the same file once per frame — 150 times for a six-second shot. FFmpeg was
#: intermittently failing exactly there: "inflate returned error -3" on PNGs
#: that decode perfectly on their own (verified: identical checksum to
#: ComfyUI's original, and a clean standalone decode), and twice an access
#: violation that killed a run outright. Measured over five attempts each on
#: the file that had just crashed a run: `-loop 1` produced decoder errors on
#: three, this produced none.
#:
#: Decoding once and repeating the decoded frame removes the mechanism rather
#: than working around it, and keeps the picture lossless — which switching
#: the intermediate to JPEG (also measured clean) would not have.
PHOTO_LOOP_FILTER = "loop=loop=-1:size=1"

#: How far a source's aspect ratio may sit from the output frame's and still
#: count as already the right shape. Generated pictures are produced at an
#: exact 9:16 (config.BACKGROUND_WIDTH x BACKGROUND_HEIGHT), so this only has
#: to absorb rounding, not judge near-misses.
ASPECT_TOLERANCE = 0.002


def probe_aspect_ratio(visual_path: str) -> float | None:
    """Width over height of a source, or None if it cannot be read.

    None rather than an exception: this only decides which of two correct
    filter chains to use, so an unreadable header should fall through to the
    general one rather than lose the shot.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                visual_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        width, height = result.stdout.strip().splitlines()[0].split("x")
        return int(width) / int(height)
    except Exception:  # noqa: BLE001 - boundary: an external tool on an arbitrary file
        return None


def _fit_filter_for(visual_path: str, multiplier: int = 1) -> str:
    """The filter chain that puts this particular source into the frame.

    A source already shaped like the frame has no leftover space, so the
    blurred-backdrop chain below builds an 8-megapixel frame, blurs it with a
    wide-radius gaussian, and overlays a picture that covers it completely —
    every pixel of that work is thrown away. That is most photo shots once a
    background has been generated, since generated pictures come out at
    exactly the output ratio.

    (This was also suspected of causing the intermittent FFmpeg crashes on
    generated shots. It was not — see PHOTO_LOOP_FILTER below for what was —
    but building and blurring eight megapixels per frame to cover every one
    of them is worth not doing regardless.)
    """
    aspect = probe_aspect_ratio(visual_path)
    frame_aspect = FRAME_WIDTH / FRAME_HEIGHT
    if aspect is not None and abs(aspect - frame_aspect) < ASPECT_TOLERANCE:
        return f"scale={FRAME_WIDTH * multiplier}:{FRAME_HEIGHT * multiplier}"
    return _fit_to_frame(multiplier)


def _fit_to_frame(multiplier: int = 1) -> str:
    """Filter chain putting any source into the 9:16 output frame.

    Filling the frame by scaling up and cropping loses most of a landscape
    source: a 4:3 photo has to reach 2640px wide before it covers 1080x1920,
    so cropping back to 1080 keeps 41% of the picture and throws the rest
    away — including, often, half the pet. So the picture is fitted whole
    and the leftover space is filled with a blurred, zoomed copy of itself
    rather than by cutting into it (config.SCENE_FIT_MODE picks the
    behaviour; "pad" uses flat black instead, "crop" restores the old
    fill-and-cut).

    Portrait sources already match the frame and come out unchanged either
    way — this only decides what happens to the ones that don't.

    multiplier builds the frame oversized: the Ken Burns move samples its
    zoom from this, and zooming into a picture already reduced to output
    resolution is what makes it soft.
    """
    width, height = FRAME_WIDTH * multiplier, FRAME_HEIGHT * multiplier
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if config.SCENE_FIT_MODE == "crop":
        return cover

    contain = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    if config.SCENE_FIT_MODE == "pad":
        return f"{contain},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"

    # split feeds the same frame to both branches: one becomes the blurred
    # backdrop (cover-cropped, so it always fills), the other the untouched
    # picture laid over it.
    return (
        f"split=2[fitbg][fitfg];"
        f"[fitbg]{cover},gblur=sigma={config.SCENE_FIT_BLUR * multiplier}[fitbgb];"
        f"[fitfg]{contain}[fitfgs];"
        f"[fitbgb][fitfgs]overlay=(W-w)/2:(H-h)/2"
    )


def build_scene_clip(
    *,
    visual_path: str,
    duration: float,
    subtitle_text: str,
    output_path: str,
    disclosure_text: str | None = None,
) -> str:
    """Render one scene's video (no audio): real video or photo (Ken Burns
    zoom) with burned subtitle. Real-footage-first per
    docs/architecture.md §5 strategy A — no AI video generation in PoC.
    Audio (narration + music) is assembled separately and muxed in at the
    end — see concat_video_only / mux_video_audio and pipeline/audio_mix.py.

    disclosure_text is burned into the top of the frame for shots whose
    setting was generated rather than photographed (docs/architecture.md §5
    strategy C). It is deliberately a parameter rather than something this
    function decides: only the caller knows how a given shot was made, and a
    label that appeared on shots that didn't need one would stop meaning
    anything. Smaller and lighter than the subtitle — it has to be legible
    and unmissable, not compete with the message."""
    is_photo = Path(visual_path).suffix.lower() in PHOTO_EXTENSIONS

    if is_photo:
        frames = max(int(duration * FRAME_RATE), 1)
        video_filter = (
            f"{PHOTO_LOOP_FILTER},"
            f"{_fit_filter_for(visual_path, PHOTO_SUPERSAMPLE)},"
            f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}:s={FRAME_WIDTH}x{FRAME_HEIGHT}:fps={FRAME_RATE},"
        )
        video_input = ["-i", visual_path]
    else:
        video_filter = f"{_fit_filter_for(visual_path)},fps={FRAME_RATE},"
        # Real clips are often shorter than the scene's assigned duration;
        # loop so -t below can always fill the full scene length.
        video_input = ["-stream_loop", "-1", "-i", visual_path]

    subtitle_file = _write_text_file(subtitle_text, output_path, suffix=".subtitle.txt")
    drawtext = (
        f"drawtext=fontfile='{_escape_filter_path(config.DRAWTEXT_FONT_FILE)}':"
        f"textfile='{_escape_filter_path(str(subtitle_file))}':"
        # expansion=none: subtitle text is copy, not a template — a literal
        # "%" or "{" in it must not be interpreted by drawtext.
        "expansion=none:"
        "fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=12:"
        "x=(w-text_w)/2:y=h-260"
    )

    if disclosure_text:
        disclosure_file = _write_text_file(disclosure_text, output_path, suffix=".disclosure.txt")
        drawtext += (
            f",drawtext=fontfile='{_escape_filter_path(config.DRAWTEXT_FONT_FILE)}':"
            f"textfile='{_escape_filter_path(str(disclosure_file))}':"
            "expansion=none:"
            "fontcolor=white:fontsize=32:box=1:boxcolor=black@0.45:boxborderw=8:"
            "x=(w-text_w)/2:y=80"
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


def build_recap_clip(
    *,
    visual_paths: list[str],
    duration: float,
    subtitle_text: str,
    output_path: str,
) -> str:
    """Render one shot that cuts through several assets in turn.

    The closing recap (pipeline/montage.py) shows the assets the story shots
    had no room for. Each asset becomes an ordinary scene clip of an equal
    slice of the shot, so the Ken Burns move, the subtitle burn-in and the
    constant frame rate are the ones every other clip gets — they are then
    concatenated into the single clip the scene loop expects back.
    """
    per_asset = duration / len(visual_paths)
    out = Path(output_path)
    parts = []
    for index, visual_path in enumerate(visual_paths, start=1):
        part = out.with_name(f"{out.stem}_part{index}{out.suffix}")
        parts.append(
            build_scene_clip(
                visual_path=visual_path,
                duration=per_asset,
                subtitle_text=subtitle_text,
                output_path=str(part),
            )
        )
    return concat_video_only(parts, output_path)


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

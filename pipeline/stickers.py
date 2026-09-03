"""Small drawn marks laid over a shot: hearts, paw prints, sparkles.

The cute half of docs/architecture.md §4's 字幕/貼圖/特效. What makes this
worth doing where a cartoon *background* was not: the pet in these videos is
a photograph, and a photographic animal on an illustrated scene reads worse
than either style on its own — it is the same cut-out-pasted-on look the
identity check exists to catch. A small flat mark in the corner does not
compete with the photograph, it frames it, which is how pet videos on social
platforms are actually decorated.

The shapes are drawn here rather than shipped as artwork, for three reasons:
nothing binary goes into version control, they are tinted to whatever accent
the video is using so they belong to it rather than sitting on top of it,
and they are deterministic — the same video always gets the same marks.

They are drawn flat and simple on purpose. This is not a substitute for a
designer's sticker set; if hand-drawn artwork ever arrives, it drops into
the same overlay slots.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from pipeline import config
from pipeline.layout import Occupancy, pick_slots


#: Where a shot may carry a mark without covering something that has to be
#: read. The top of the frame belongs to the pet's details and the
#: AI-generation disclosure; the bottom belongs to the subtitle. Everything
#: here sits in the band between them, at the edges, away from the middle
#: where the animal usually is.
def placement_slots(frame_width: int, frame_height: int, size: int) -> list[tuple[int, int]]:
    margin = config.DECOR_STICKER_MARGIN
    top = config.DECOR_STICKER_SAFE_TOP
    bottom = frame_height - config.DECOR_STICKER_SAFE_BOTTOM - size
    return [
        (margin, top),
        (frame_width - margin - size, top),
        (margin, bottom),
        (frame_width - margin - size, bottom),
    ]


def _rgba(colour: str, opacity: float) -> tuple[int, int, int, int]:
    """Turn an FFmpeg-style 0xRRGGBB into an RGBA tuple at this opacity."""
    value = colour.lower().removeprefix("0x").removeprefix("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return red, green, blue, int(max(0.0, min(1.0, opacity)) * 255)


def _heart(draw, size: int, fill, outline) -> None:
    points = []
    for step in range(180):
        angle = step / 180 * 2 * math.pi
        x = 16 * math.sin(angle) ** 3
        y = (
            13 * math.cos(angle)
            - 5 * math.cos(2 * angle)
            - 2 * math.cos(3 * angle)
            - math.cos(4 * angle)
        )
        points.append((size / 2 + x * size / 42, size / 2 - y * size / 42))
    draw.polygon(points, fill=fill, outline=outline)


def _paw(draw, size: int, fill, outline) -> None:
    """A pad and four toes — the one shape that says "pet" without a word."""
    unit = size / 10
    draw.ellipse([2.2 * unit, 4.4 * unit, 7.8 * unit, 9.2 * unit], fill=fill, outline=outline)
    for centre_x, centre_y, radius in (
        (2.6, 3.4, 1.15),
        (4.4, 2.2, 1.2),
        (6.4, 2.4, 1.2),
        (8.0, 4.0, 1.05),
    ):
        draw.ellipse(
            [
                (centre_x - radius) * unit,
                (centre_y - radius) * unit,
                (centre_x + radius) * unit,
                (centre_y + radius) * unit,
            ],
            fill=fill,
            outline=outline,
        )


def _sparkle(draw, size: int, fill, outline) -> None:
    """A four-pointed star with concave sides — reads as a glint rather than
    as a rating star, which would mean something it does not."""
    centre = size / 2
    long_arm, short_arm = size * 0.48, size * 0.12
    points = []
    for index in range(8):
        angle = index * math.pi / 4 - math.pi / 2
        arm = long_arm if index % 2 == 0 else short_arm
        points.append((centre + arm * math.cos(angle), centre + arm * math.sin(angle)))
    draw.polygon(points, fill=fill, outline=outline)


_SHAPES = {"heart": _heart, "paw": _paw, "sparkle": _sparkle}


def sticker_path(shape: str, accent: str, size: int | None = None) -> Path:
    """Draw this shape in this colour, or hand back the one already drawn.

    Cached by shape/colour/size under storage/decor/ rather than committed:
    the marks are generated art, and regenerating them costs milliseconds.
    """
    from PIL import Image, ImageDraw

    if shape not in _SHAPES:
        raise ValueError(f"Unknown sticker shape {shape!r}, expected one of {sorted(_SHAPES)}")

    size = size or config.DECOR_STICKER_SIZE
    fill = _rgba(accent, config.DECOR_STICKER_OPACITY)
    outline = (255, 255, 255, int(config.DECOR_STICKER_OPACITY * 235))

    key = hashlib.sha256(f"{shape}|{accent}|{size}|{fill}|{outline}".encode()).hexdigest()[:12]
    target = config.DECOR_DIR / f"{shape}_{key}.png"
    if target.exists():
        return target

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _SHAPES[shape](ImageDraw.Draw(image), size, fill, outline)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


def stickers_for_scene(
    style: str,
    accent: str,
    scene_index: int,
    frame_width: int,
    frame_height: int,
    occupancy: Occupancy | None = None,
) -> list[tuple[Path, int, int]]:
    """The marks this shot carries, and where they sit.

    Corners are chosen by what is under them: the emptiest first, each one
    taken out of the running as it is picked so two marks cannot land on the
    same clear corner. A mark over the animal's face is the most obviously
    wrong thing this layer can do, and it used to happen whenever the
    rotation landed there.

    Without an occupancy the corners rotate with the shot, as before, so a
    six-shot video still does not have the same mark stuck in one place six
    times — that is the fallback when nothing knows where the pet is.

    Returns an empty list when stickers are off or the style asks for none —
    a style that wants a plain frame is a real answer.
    """
    if not config.DECOR_STICKERS_ENABLED:
        return []

    shapes = config.DECOR_STICKER_SETS.get(style, config.DECOR_STICKER_SETS.get("", []))
    if not shapes:
        return []

    size = config.DECOR_STICKER_SIZE
    slots = placement_slots(frame_width, frame_height, size)

    if occupancy is None:
        positions = [slots[(scene_index + offset) % len(slots)] for offset in range(len(shapes))]
    else:
        boxes = [
            (x / frame_width, y / frame_height, (x + size) / frame_width, (y + size) / frame_height)
            for x, y in slots
        ]
        positions = [
            (int(box[0] * frame_width), int(box[1] * frame_height))
            for box in pick_slots(boxes, occupancy, len(shapes))
        ]

    return [
        (sticker_path(shape, accent, size), x, y)
        for shape, (x, y) in zip(shapes, positions, strict=False)
    ]

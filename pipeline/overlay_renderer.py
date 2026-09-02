"""Composed UI layers over a shot: information panels, speech bubbles, quotes.

The half of docs/architecture.md §4's 字幕/貼圖/特效 that drawtext cannot
reach. A subtitle is one string at one position, so a drawtext filter is
exactly the right tool for it. A panel is not: it is a rounded translucent
plate whose height depends on how many lines its content wrapped to, a rule
in the video's accent colour, a stack of measured lines, and — for a bubble —
a tail pointing back at the animal. Writing that as drawbox+drawtext means
computing every coordinate in FFmpeg's expression syntax, inside a chain that
is already long, with no way to ask how wide a string will render.

So the same split as pipeline/stickers.py, one step up: Pillow lays the piece
out because Pillow can measure text, and hands FFmpeg a single transparent
PNG to composite. pipeline/editing.py lays it over the finished frame in the
same single -vf chain as everything else, so a shot wearing a panel still
costs one encode.

Like the rest of the decoration and unlike a replaced background, this is
deterministic, cannot alter the animal, and needs no AI-generation
disclosure. What it does need is fact-checking: "疫苗：已完成" burned into the
frame is a claim about a real animal exactly as much as the same words spoken
in the narration, which is why pipeline/fact_check.py reads these fields
alongside narration and subtitle.

Which template a shot wears is the script's decision (CLAUDE.md: 畫面上的創作
決定要寫在腳本裡) — what each template *looks* like is a design system and
lives in config.OVERLAY_*, not in something a 7B model invents per run.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from pipeline import config
from pipeline.editing import text_tokens


class OverlayTemplate(str, enum.Enum):
    """Which composed layer a shot wears.

    str-valued for the same reason BackgroundMode is: it round-trips through
    the script JSON, the job row and an API response without conversion.
    """

    #: Nothing beyond the subtitle. The ordinary case — a panel on every shot
    #: is a slideshow, not a video.
    NONE = "none"
    #: A list of short facts down one side: age, vaccination, temperament.
    INFO_SIDEBAR = "info_sidebar"
    #: A short line the pet is saying, in a bubble with a tail.
    SPEECH_BUBBLE = "speech_bubble"
    #: One line held large in the middle of the frame. The opening hook.
    CENTER_QUOTE = "center_quote"
    #: The closing ask: what to do next and who to contact.
    CONTACT_CARD = "contact_card"


#: Which field each template cannot be rendered without. A template naming a
#: field the script left empty produces a plate with nothing on it, which is
#: worse than no plate at all — pipeline/qa.py reports these and
#: resolve_scene_overlay() drops them.
REQUIRED_FIELDS: dict[OverlayTemplate, tuple[str, ...]] = {
    OverlayTemplate.INFO_SIDEBAR: ("tags",),
    OverlayTemplate.SPEECH_BUBBLE: ("quote",),
    OverlayTemplate.CENTER_QUOTE: ("headline",),
    OverlayTemplate.CONTACT_CARD: ("cta_text",),
}


class SceneOverlaySpec(BaseModel):
    """What one shot's composed layer says.

    Copy only — no coordinates, no colours, no sizes. Those belong to the
    design system in config.OVERLAY_*, and a model that cannot see the frame
    is in no position to choose them.
    """

    template: OverlayTemplate = OverlayTemplate.NONE
    #: One line held large: the hook.
    headline: str | None = None
    #: Something the pet says, for a bubble.
    quote: str | None = None
    #: Short facts, one per line. Truncated to config.OVERLAY_MAX_TAGS.
    tags: list[str] = Field(default_factory=list)
    #: The closing ask ("預約見面").
    cta_text: str | None = None
    #: Who to contact, under the ask.
    contact_info: str | None = None

    def has_content(self) -> bool:
        """Whether the template's own required field was actually filled."""
        for field in REQUIRED_FIELDS.get(self.template, ()):
            value = getattr(self, field)
            if isinstance(value, list):
                if any(item and item.strip() for item in value):
                    return True
            elif value and value.strip():
                return True
        return False

    def spoken_text(self) -> str:
        """Every string this layer burns into the frame.

        Fact-checking reads this: a claim is a claim whether it is narrated or
        printed on a panel, and the panel version is the one a viewer watching
        on mute actually receives.
        """
        parts = [self.headline, self.quote, self.cta_text, self.contact_info, *self.tags]
        return " ".join(part.strip() for part in parts if part and part.strip())


def resolve_scene_overlay(scene: dict) -> SceneOverlaySpec | None:
    """The composed layer this shot wears, or None for none at all.

    Deliberately narrower than resolve_scene_background(): there is no CLI
    override to reconcile, because a reviewer correcting the wording of a
    panel edits the script's own field through single-shot regeneration — the
    text is the content, not a rendering setting.

    An unusable template or an empty required field is not worth failing a
    render over. The shot is made without a panel and pipeline/qa.py reports
    it, the same way an unknown background mode is handled.
    """
    block = scene.get("overlay")
    if not isinstance(block, dict):
        return None

    try:
        spec = SceneOverlaySpec.model_validate(block)
    except ValidationError:
        # Boundary: the block is model-written JSON. A malformed one costs
        # the panel, never the shot.
        return None

    if spec.template is OverlayTemplate.NONE or not spec.has_content():
        return None
    return spec


# --- drawing ----------------------------------------------------------------


def _rgba(colour: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    """Turn an FFmpeg-style 0xRRGGBB into an RGBA tuple at this opacity.

    The accent reaching here is the same string the border and the stickers
    are drawn in (config.DECOR_PALETTES, or the reviewer's own override), so
    it arrives in FFmpeg's colour notation rather than Pillow's.
    """
    value = colour.lower().removeprefix("0x").removeprefix("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return red, green, blue, int(max(0.0, min(1.0, opacity)) * 255)


#: Codepoint ranges the panel font has no glyphs for.
#:
#: OVERLAY_FONT_FILE is a CJK *text* face (msjh by default), so an emoji in
#: model-written copy renders as .notdef — a hollow tofu box. Measured: a
#: contact card reading "預約見面 🐾" came out as "預約見面 □", which looks
#: like a broken video rather than a missing decoration.
#:
#: Stripped by range rather than by probing the font for coverage, because
#: the answer has to be the same on every machine: a host whose font happens
#: to carry one emoji would otherwise produce a different picture from the
#: next. The script prompt also asks for no emoji, but as with the subtitle
#: length limit, the picture must not depend on a 7B model obeying it.
_UNRENDERABLE_RANGES = (
    (0x1F000, 0x1FAFF),  # emoticons, pictographs, symbols, extended-A
    (0x2600, 0x27BF),  # misc symbols and dingbats
    (0xFE00, 0xFE0F),  # variation selectors
    (0xE000, 0xF8FF),  # private use
    (0x200D, 0x200D),  # zero-width joiner, left behind by stripped sequences
)


def strip_unrenderable(text: str) -> str:
    """Drop the characters the panel font cannot draw."""
    kept = [
        char
        for char in text
        if not any(low <= ord(char) <= high for low, high in _UNRENDERABLE_RANGES)
    ]
    return "".join(kept).strip()


def _clip(text: str | None) -> str:
    """One field's worth of model-written copy, bounded and renderable.

    Pillow wraps instead of clipping, so an overlong string does not run off
    the frame — it grows the plate until it covers the animal. The bound is on
    the input for that reason, and it is a truncation rather than a rejection:
    losing the tail of one line is recoverable, losing the shot is not.
    """
    text = strip_unrenderable((text or "").strip())
    if len(text) > config.OVERLAY_MAX_CHARS:
        return text[: config.OVERLAY_MAX_CHARS - 1] + "…"
    return text


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(config.OVERLAY_FONT_FILE, size)
    except OSError:
        # Boundary: the font path is configuration pointing at the host
        # filesystem. A missing font must not cost the video — the panel
        # renders in the bitmap default, visibly worse and obviously wrong,
        # which is the right way for a misconfiguration to show up.
        return ImageFont.load_default()


def _line_height(font) -> int:
    """Height of one line in this font, measured rather than assumed.

    A CJK face's ascent/descent bear little relation to the point size, and
    stacking lines by point size alone overlaps them.
    """
    bbox = font.getbbox("測Ay")
    return bbox[3] - bbox[1] + config.OVERLAY_LINE_GAP


def _text_width(draw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def wrap_to_width(draw, text: str, font, max_width: int) -> list[str]:
    """Break copy into lines that fit max_width pixels.

    The same question editing.wrap_burned_text answers for drawtext, with a
    better ruler: there the width has to be estimated in half-width units
    because FFmpeg cannot report it, here the font itself can be measured.
    Latin breaks at spaces, CJK between characters — mixed copy hits both.
    """
    lines: list[str] = []
    current = ""
    for token, separator in text_tokens(text):
        candidate = current + separator + token if current else token
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _plate(draw, box: tuple[int, int, int, int], accent: str) -> None:
    """The translucent rounded ground every template stands on.

    White rather than accent-coloured: the copy on it has to stay
    black-on-light whatever accent the video is using, and an accent that
    happened to be dark would make its own panel unreadable. The accent shows
    up as the outline, which is what makes the panel part of this video rather
    than a generic caption box.
    """
    draw.rounded_rectangle(
        box,
        radius=config.OVERLAY_PANEL_RADIUS,
        fill=(255, 255, 255, int(config.OVERLAY_PANEL_OPACITY * 255)),
        outline=_rgba(accent),
        width=config.OVERLAY_OUTLINE_WIDTH,
    )


def _draw_lines(draw, lines: list[str], x: int, y: int, font, colour) -> int:
    """Stack lines from y downward, returning the y just past the last one."""
    step = _line_height(font)
    for line in lines:
        draw.text((x, y), line, font=font, fill=colour)
        y += step
    return y


def _band(height: int) -> tuple[int, int]:
    """The vertical range a panel may occupy.

    Above it sit the pet's details and the AI-generation disclosure, below it
    the subtitle. Anything outside covers something a viewer has to read — the
    same reasoning, and the same numbers, as the stickers' safe zone.
    """
    return config.OVERLAY_SAFE_TOP, height - config.OVERLAY_SAFE_BOTTOM


def _render_info_sidebar(draw, spec, width, height, accent, text_colour) -> None:
    """A column of short facts down the right-hand side.

    Right rather than left, and only as wide as OVERLAY_SIDEBAR_RATIO: the
    animal is what the viewer came for, and a panel is worth the picture it
    covers only if it leaves the picture.
    """
    font = _font(config.OVERLAY_BODY_SIZE)
    padding = config.OVERLAY_PANEL_PADDING
    panel_width = int(width * config.OVERLAY_SIDEBAR_RATIO)
    inner_width = panel_width - padding * 2

    tags = [_clip(tag) for tag in spec.tags if tag and tag.strip()][: config.OVERLAY_MAX_TAGS]
    lines: list[str] = []
    for tag in tags:
        lines.extend(wrap_to_width(draw, tag, font, inner_width))

    band_top, _ = _band(height)
    x0 = width - panel_width - config.OVERLAY_MARGIN
    y0 = band_top
    panel_height = padding * 2 + _line_height(font) * len(lines)

    _plate(draw, (x0, y0, x0 + panel_width, y0 + panel_height), accent)
    _draw_lines(draw, lines, x0 + padding, y0 + padding, font, text_colour)


def _render_speech_bubble(draw, spec, width, height, accent, text_colour) -> None:
    """Something the pet is saying, with a tail so it reads as speech.

    The tail is the whole difference between a bubble and a caption box, and
    it is why this template is a drawn PNG rather than a drawbox: it is a
    triangle whose apex has to sit under the plate and point into the picture.
    """
    font = _font(config.OVERLAY_QUOTE_SIZE)
    padding = config.OVERLAY_PANEL_PADDING
    band_top, band_bottom = _band(height)

    max_inner = int(width * 0.62) - padding * 2
    lines = wrap_to_width(draw, _clip(spec.quote), font, max_inner)
    inner_width = max(_text_width(draw, line, font) for line in lines)
    panel_width = inner_width + padding * 2
    panel_height = padding * 2 + _line_height(font) * len(lines)

    # Upper part of the band, so the tail has picture to point down into
    # rather than pointing at the subtitle.
    x0 = width - panel_width - config.OVERLAY_MARGIN
    y0 = band_top + int((band_bottom - band_top) * 0.12)

    _plate(draw, (x0, y0, x0 + panel_width, y0 + panel_height), accent)

    tail_x = x0 + panel_width // 3
    draw.polygon(
        [
            (tail_x, y0 + panel_height - config.OVERLAY_OUTLINE_WIDTH),
            (tail_x + config.OVERLAY_BUBBLE_TAIL_WIDTH, y0 + panel_height),
            (
                tail_x - config.OVERLAY_BUBBLE_TAIL_WIDTH // 3,
                y0 + panel_height + config.OVERLAY_BUBBLE_TAIL_HEIGHT,
            ),
        ],
        fill=(255, 255, 255, int(config.OVERLAY_PANEL_OPACITY * 255)),
        outline=_rgba(accent),
    )
    _draw_lines(draw, lines, x0 + padding, y0 + padding, font, text_colour)


def _render_center_quote(draw, spec, width, height, accent, text_colour) -> None:
    """One line held large across the middle: the opening hook.

    Centred both ways within the band, because this is the only template that
    is the shot's message rather than an annotation on it.
    """
    font = _font(config.OVERLAY_HEADLINE_SIZE)
    padding = config.OVERLAY_PANEL_PADDING
    band_top, band_bottom = _band(height)

    max_inner = width - config.OVERLAY_MARGIN * 2 - padding * 2
    lines = wrap_to_width(draw, _clip(spec.headline), font, max_inner)
    inner_width = max(_text_width(draw, line, font) for line in lines)
    panel_width = inner_width + padding * 2
    panel_height = padding * 2 + _line_height(font) * len(lines)

    x0 = (width - panel_width) // 2
    y0 = band_top + (band_bottom - band_top - panel_height) // 2

    _plate(draw, (x0, y0, x0 + panel_width, y0 + panel_height), accent)
    step = _line_height(font)
    y = y0 + padding
    for line in lines:
        line_x = x0 + (panel_width - _text_width(draw, line, font)) // 2
        draw.text((line_x, y), line, font=font, fill=text_colour)
        y += step


def _render_contact_card(draw, spec, width, height, accent, text_colour) -> None:
    """The closing ask, and who to ask.

    Sits at the bottom of the band, just above where the subtitle starts: this
    is the last thing on screen and the one the viewer is meant to act on, so
    it belongs where the eye already is.
    """
    cta_font = _font(config.OVERLAY_QUOTE_SIZE)
    contact_font = _font(config.OVERLAY_BODY_SIZE)
    padding = config.OVERLAY_PANEL_PADDING
    _, band_bottom = _band(height)

    max_inner = width - config.OVERLAY_MARGIN * 2 - padding * 2
    cta_lines = wrap_to_width(draw, _clip(spec.cta_text), cta_font, max_inner)
    contact_lines = (
        wrap_to_width(draw, _clip(spec.contact_info), contact_font, max_inner)
        if spec.contact_info and spec.contact_info.strip()
        else []
    )

    inner_width = max(
        [_text_width(draw, line, cta_font) for line in cta_lines]
        + [_text_width(draw, line, contact_font) for line in contact_lines]
    )
    panel_width = inner_width + padding * 2
    panel_height = (
        padding * 2
        + _line_height(cta_font) * len(cta_lines)
        + _line_height(contact_font) * len(contact_lines)
    )

    x0 = (width - panel_width) // 2
    # Lifted clear of the band's floor rather than sitting on it. The band's
    # bottom edge was set for stickers, which are small and sparse; a plate
    # flush against it reads as one block with the subtitle beneath, and a
    # subtitle that wraps to a third line grows upward into it (the subtitle
    # is anchored by the bottom of its text block, see editing.py). Measured
    # on a real shot with a two-line subtitle: the two were touching.
    y0 = band_bottom - config.OVERLAY_SUBTITLE_CLEARANCE - panel_height

    _plate(draw, (x0, y0, x0 + panel_width, y0 + panel_height), accent)
    y = _draw_lines(draw, cta_lines, x0 + padding, y0 + padding, cta_font, _rgba(accent))
    # A rule in the accent between the ask and the contact: two type sizes
    # alone read as one paragraph that happens to shrink.
    if contact_lines:
        draw.line(
            [(x0 + padding, y + 4), (x0 + panel_width - padding, y + 4)],
            fill=_rgba(accent),
            width=2,
        )
        _draw_lines(draw, contact_lines, x0 + padding, y + 14, contact_font, text_colour)


_RENDERERS: dict[OverlayTemplate, Any] = {
    OverlayTemplate.INFO_SIDEBAR: _render_info_sidebar,
    OverlayTemplate.SPEECH_BUBBLE: _render_speech_bubble,
    OverlayTemplate.CENTER_QUOTE: _render_center_quote,
    OverlayTemplate.CONTACT_CARD: _render_contact_card,
}


def render_scene_overlay(
    spec: SceneOverlaySpec,
    *,
    width: int,
    height: int,
    accent: str,
    output_path: Path | str,
) -> Path | None:
    """Draw one shot's composed layer as a transparent PNG, or None.

    Full-frame rather than a cropped sprite: the templates place themselves
    against the frame's own safe zones, so the file FFmpeg gets is laid on at
    0:0 with no coordinates to keep in sync between the two languages.
    Returns None when there is nothing to draw, which the caller reads as
    "leave the picture alone".
    """
    from PIL import Image, ImageDraw

    if not config.OVERLAY_ENABLED or spec.template is OverlayTemplate.NONE:
        return None
    renderer = _RENDERERS.get(spec.template)
    if renderer is None or not spec.has_content():
        return None

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    renderer(draw, spec, width, height, accent, _rgba(config.OVERLAY_TEXT_COLOUR))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG")
    return output_path

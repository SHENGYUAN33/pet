from __future__ import annotations

import pytest

from pipeline import config
from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH
from pipeline.overlay_renderer import (
    OverlayTemplate,
    SceneOverlaySpec,
    render_scene_overlay,
    resolve_scene_overlay,
)

ACCENT = "0xFF8FA3"


def _render(spec: SceneOverlaySpec, tmp_path):
    return render_scene_overlay(
        spec,
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        accent=ACCENT,
        output_path=tmp_path / "overlay.png",
    )


def _opaque_rows(path) -> list[int]:
    """Which frame rows the layer actually painted on."""
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    return [
        y for y in range(image.height) if alpha.crop((0, y, image.width, y + 1)).getextrema()[1]
    ]


# --- resolve ----------------------------------------------------------------


def test_scene_without_overlay_block_gets_none():
    assert resolve_scene_overlay({"scene_id": 1}) is None


def test_template_none_is_not_an_overlay():
    assert resolve_scene_overlay({"overlay": {"template": "none"}}) is None


def test_unknown_template_is_dropped_rather_than_raising():
    # The shot still gets made; pipeline/qa.py is what tells the reviewer.
    assert resolve_scene_overlay({"overlay": {"template": "hologram"}}) is None


def test_template_without_its_required_field_is_dropped():
    assert resolve_scene_overlay({"overlay": {"template": "speech_bubble", "quote": "  "}}) is None
    assert resolve_scene_overlay({"overlay": {"template": "info_sidebar", "tags": []}}) is None


def test_filled_template_resolves():
    spec = resolve_scene_overlay({"overlay": {"template": "center_quote", "headline": "等你來"}})
    assert spec is not None
    assert spec.template is OverlayTemplate.CENTER_QUOTE


# --- fact-checkable text ----------------------------------------------------


def test_spoken_text_includes_every_burned_field():
    spec = SceneOverlaySpec(
        template=OverlayTemplate.INFO_SIDEBAR,
        tags=["年齡：2歲", "疫苗：已完成"],
        cta_text="預約見面",
        contact_info="範例動物之家",
    )
    text = spec.spoken_text()
    # A claim printed on a panel is a claim: pipeline/fact_check.py reads this.
    assert "疫苗：已完成" in text
    assert "範例動物之家" in text


# --- drawing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "name"),
    [
        (
            SceneOverlaySpec(
                template=OverlayTemplate.CENTER_QUOTE, headline="我在這裡，等你的溫暖"
            ),
            "quote",
        ),
        (SceneOverlaySpec(template=OverlayTemplate.SPEECH_BUBBLE, quote="喜歡呼嚕嚕"), "bubble"),
        (
            SceneOverlaySpec(
                template=OverlayTemplate.INFO_SIDEBAR,
                tags=["年齡：2歲", "疫苗：已完成", "性格：穩重親人"],
            ),
            "sidebar",
        ),
        (
            SceneOverlaySpec(
                template=OverlayTemplate.CONTACT_CARD,
                cta_text="預約見面",
                contact_info="溫暖貓咪領養中心",
            ),
            "contact",
        ),
    ],
)
def test_every_template_draws_a_transparent_full_frame_png(spec, name, tmp_path):
    from PIL import Image

    path = render_scene_overlay(
        spec,
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        accent=ACCENT,
        output_path=tmp_path / f"{name}.png",
    )
    assert path is not None and path.exists()

    image = Image.open(path)
    # Full-frame and RGBA: editing.py lays it on at 0:0 and relies on the
    # alpha channel for everything it is not covering.
    assert image.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert image.mode == "RGBA"
    assert image.getchannel("A").getextrema() == (0, 255)


@pytest.mark.parametrize(
    "spec",
    [
        SceneOverlaySpec(template=OverlayTemplate.CENTER_QUOTE, headline="我在這裡，等你的溫暖"),
        SceneOverlaySpec(template=OverlayTemplate.SPEECH_BUBBLE, quote="喜歡呼嚕嚕"),
        SceneOverlaySpec(template=OverlayTemplate.INFO_SIDEBAR, tags=["年齡：2歲", "疫苗：已完成"]),
        SceneOverlaySpec(template=OverlayTemplate.CONTACT_CARD, cta_text="預約見面"),
    ],
)
def test_no_template_lands_on_the_subtitle_or_the_info_card(spec, tmp_path):
    """The band exists because the top and bottom of the frame are spoken for.

    A panel over the burned-in subtitle covers the message the video is
    carrying to a muted viewer, and one over the pet's details covers the
    first thing anyone reads.
    """
    rows = _opaque_rows(_render(spec, tmp_path))
    assert rows, "template drew nothing"
    assert min(rows) >= config.OVERLAY_SAFE_TOP
    assert max(rows) <= FRAME_HEIGHT - config.OVERLAY_SAFE_BOTTOM


def test_panel_grows_with_its_content_rather_than_overflowing(tmp_path):
    """The whole reason this is laid out in Pillow: height follows the text."""
    short = _opaque_rows(
        _render(
            SceneOverlaySpec(template=OverlayTemplate.INFO_SIDEBAR, tags=["年齡：2歲"]), tmp_path
        )
    )
    long = _opaque_rows(
        _render(
            SceneOverlaySpec(
                template=OverlayTemplate.INFO_SIDEBAR,
                tags=["年齡：2歲", "疫苗：已完成", "性格：穩重親人"],
            ),
            tmp_path,
        )
    )
    assert len(long) > len(short)


def test_overlong_copy_is_truncated_instead_of_covering_the_pet(tmp_path):
    rows = _opaque_rows(
        _render(
            SceneOverlaySpec(template=OverlayTemplate.CENTER_QUOTE, headline="等" * 400),
            tmp_path,
        )
    )
    assert max(rows) <= FRAME_HEIGHT - config.OVERLAY_SAFE_BOTTOM


def test_bubble_has_a_tail_below_its_plate(tmp_path):
    """A bubble without a tail is a caption box, which is what drawtext
    already does — the tail is the reason this template exists."""
    from PIL import Image

    path = _render(
        SceneOverlaySpec(template=OverlayTemplate.SPEECH_BUBBLE, quote="喜歡呼嚕嚕"), tmp_path
    )
    alpha = Image.open(path).convert("RGBA").getchannel("A")

    widths = []
    for y in _opaque_rows(path):
        # histogram rather than getdata: bucket 0 is fully transparent, so
        # everything above it is a pixel this row actually painted.
        row = alpha.crop((0, y, alpha.width, y + 1))
        widths.append(sum(row.histogram()[1:]))

    # The tail is narrower than the plate it hangs off, so the last rows are
    # a fraction of the widest ones.
    assert widths[-1] < max(widths) / 3


def test_disabled_overlays_draw_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OVERLAY_ENABLED", False)
    spec = SceneOverlaySpec(template=OverlayTemplate.CENTER_QUOTE, headline="等你來")
    assert _render(spec, tmp_path) is None


def test_emoji_are_stripped_rather_than_drawn_as_tofu():
    """Measured on a real shot: "預約見面 🐾" rendered as "預約見面 □" in the
    CJK text face, which reads as a broken video rather than a missing
    decoration."""
    from pipeline.overlay_renderer import strip_unrenderable

    assert strip_unrenderable("預約見面 🐾") == "預約見面"
    assert strip_unrenderable("我在這裡 ✨💛") == "我在這裡"
    # Ordinary copy is untouched — this must not quietly eat punctuation.
    assert strip_unrenderable("年齡：2歲（已結紮）") == "年齡：2歲（已結紮）"


def test_a_bottom_anchored_panel_keeps_clear_of_the_subtitle(tmp_path):
    """A plate resting on the band's floor reads as one block with the
    subtitle, and a subtitle wrapping to a third line grows up into it."""
    rows = _opaque_rows(
        _render(
            SceneOverlaySpec(
                template=OverlayTemplate.CONTACT_CARD,
                cta_text="預約見面",
                contact_info="範例動物之家",
            ),
            tmp_path,
        )
    )
    floor = FRAME_HEIGHT - config.OVERLAY_SAFE_BOTTOM
    assert max(rows) <= floor - config.OVERLAY_SUBTITLE_CLEARANCE

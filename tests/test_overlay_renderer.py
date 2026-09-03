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


# --- typefaces --------------------------------------------------------------


def test_each_role_asks_for_its_own_face():
    from pipeline.overlay_renderer import HANDWRITTEN, ROUND, _font_path

    assert _font_path(HANDWRITTEN) == config.OVERLAY_FONT_HANDWRITTEN
    assert _font_path(ROUND) == config.OVERLAY_FONT_ROUND


def test_an_unconfigured_role_falls_back_to_the_general_face(monkeypatch):
    from pipeline.overlay_renderer import HANDWRITTEN, _font_path

    monkeypatch.setattr(config, "OVERLAY_FONT_HANDWRITTEN", "")
    assert _font_path(HANDWRITTEN) == config.OVERLAY_FONT_FILE


def test_a_missing_font_file_still_renders(tmp_path, monkeypatch):
    """The named open-source faces are not shipped — nothing binary goes into
    version control — so the common case is a machine that does not have
    them. A missing file has to cost a typeface, never a video."""
    from pipeline.overlay_renderer import HANDWRITTEN, _font

    monkeypatch.setattr(config, "OVERLAY_FONT_HANDWRITTEN", str(tmp_path / "nope.ttf"))
    assert _font(48, HANDWRITTEN) is not None

    path = _render(
        SceneOverlaySpec(template=OverlayTemplate.CENTER_QUOTE, headline="等你來"), tmp_path
    )
    assert path is not None and path.exists()


def test_every_font_path_missing_still_renders(tmp_path, monkeypatch):
    from pipeline.overlay_renderer import ROUND, _font

    monkeypatch.setattr(config, "OVERLAY_FONT_ROUND", str(tmp_path / "a.ttf"))
    monkeypatch.setattr(config, "OVERLAY_FONT_FILE", str(tmp_path / "b.ttf"))
    assert _font(30, ROUND) is not None


# --- shadow and tilt --------------------------------------------------------


def test_a_panel_casts_a_shadow(tmp_path, monkeypatch):
    """A flat white rectangle on a photograph reads as a screenshot pasted on
    it; the shadow is what makes it a card lying on the picture."""
    from PIL import Image

    spec = SceneOverlaySpec(template=OverlayTemplate.CENTER_QUOTE, headline="等你來")

    monkeypatch.setattr(config, "OVERLAY_SHADOW_OPACITY", 0.0)
    flat = _opaque_rows(_render(spec, tmp_path / "flat"))

    monkeypatch.setattr(config, "OVERLAY_SHADOW_OPACITY", 0.15)
    shadowed_path = _render(spec, tmp_path / "shadowed")
    shadowed = _opaque_rows(shadowed_path)

    # The shadow is the panel's own alpha, blurred — so it paints rows the
    # flat version never touched.
    assert len(shadowed) > len(flat)

    # And it is soft: partially transparent pixels exist, which a hard-edged
    # plate alone would not produce.
    alpha = Image.open(shadowed_path).convert("RGBA").getchannel("A")
    partial = sum(alpha.histogram()[1:250])
    assert partial > 0


def test_the_shadow_stays_inside_the_safe_band(tmp_path):
    """The shadow is part of what the layer paints, so a panel placed flush
    against the subtitle would put its shadow on top of the subtitle."""
    rows = _opaque_rows(
        _render(
            SceneOverlaySpec(
                template=OverlayTemplate.CONTACT_CARD, cta_text="預約見面", contact_info="範例之家"
            ),
            tmp_path,
        )
    )
    assert min(rows) >= config.OVERLAY_SAFE_TOP
    assert max(rows) <= FRAME_HEIGHT - config.OVERLAY_SAFE_BOTTOM


def test_only_the_bubble_leans():
    """A tilted list of vaccination facts reads as a mistake, not a style."""
    from pipeline.overlay_renderer import tilt_for

    assert tilt_for(OverlayTemplate.SPEECH_BUBBLE, 0) != 0
    for template in (
        OverlayTemplate.CENTER_QUOTE,
        OverlayTemplate.INFO_SIDEBAR,
        OverlayTemplate.CONTACT_CARD,
    ):
        assert tilt_for(template, 0) == 0


def test_the_tilt_is_deterministic_and_alternates():
    """Fixed per shot rather than random: a resumed run has to reproduce the
    shot it is continuing, not a subtly different one — the same reason
    config.BACKGROUND_SEED is pinned."""
    from pipeline.overlay_renderer import tilt_for

    first = tilt_for(OverlayTemplate.SPEECH_BUBBLE, 0)
    assert tilt_for(OverlayTemplate.SPEECH_BUBBLE, 0) == first
    assert tilt_for(OverlayTemplate.SPEECH_BUBBLE, 1) == -first


def test_a_leaning_bubble_still_clears_the_safe_band(tmp_path):
    """Rotation swings the corners outward, so the layout has to leave room
    for it or the lean pushes the plate onto the subtitle."""
    rows = _opaque_rows(
        _render(
            SceneOverlaySpec(template=OverlayTemplate.SPEECH_BUBBLE, quote="喜歡呼嚕嚕"), tmp_path
        )
    )
    assert min(rows) >= config.OVERLAY_SAFE_TOP
    assert max(rows) <= FRAME_HEIGHT - config.OVERLAY_SAFE_BOTTOM


# --- icons ------------------------------------------------------------------


def test_icons_are_chosen_from_words_in_the_line():
    from pipeline.overlay_renderer import icon_for

    assert icon_for("年齡：2歲") == "cake"
    assert icon_for("疫苗：已完成") == "syringe"
    assert icon_for("已結紮") == "syringe"
    assert icon_for("個性：穩重親人") == "heart"


def test_an_unmatched_line_still_gets_a_mark():
    """A list where some rows are indented and others are not looks broken
    rather than minimal."""
    from pipeline.overlay_renderer import icon_for

    assert icon_for("喜歡曬太陽") == config.OVERLAY_ICON_DEFAULT


def test_icons_shift_the_text_right_rather_than_overlapping_it(tmp_path):
    from PIL import Image

    spec = SceneOverlaySpec(template=OverlayTemplate.INFO_SIDEBAR, tags=["年齡：1歲"])

    def leftmost(path) -> int:
        alpha = Image.open(path).convert("RGBA").getchannel("A")
        for x in range(alpha.width):
            if sum(alpha.crop((x, 0, x + 1, alpha.height)).histogram()[1:]):
                return x
        raise AssertionError("nothing drawn")

    with_icons = leftmost(_render(spec, tmp_path / "with"))

    import pytest as _pytest

    monkey = _pytest.MonkeyPatch()
    monkey.setattr(config, "OVERLAY_ICONS_ENABLED", False)
    try:
        without = leftmost(_render(spec, tmp_path / "without"))
    finally:
        monkey.undo()

    # The plate is the leftmost thing either way, so the panel edge is the
    # same — what matters is that turning icons on did not widen the panel,
    # i.e. the text was indented inside it rather than the icon pushing out.
    assert with_icons == without

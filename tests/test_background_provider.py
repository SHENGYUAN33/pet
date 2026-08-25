"""Margin geometry and graph wiring for the generated-background pass.

The part worth testing without a GPU is the arithmetic: the photo has to come
through the outpaint whole, and the mask handed to the sampler has to cover
the added margin and nothing else. Get either wrong and the feature quietly
becomes "crop the pet and repaint it", which is the one thing it must never
do.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline import config
from providers.image.comfy_background_provider import (
    LATENT_ALIGN,
    ComfyBackgroundProvider,
    plan_margins,
    probe_image_size,
)

FRAME = (config.BACKGROUND_WIDTH, config.BACKGROUND_HEIGHT)


def test_landscape_photo_gains_bands_above_and_below():
    margins = plan_margins(4000, 3000, *FRAME)

    assert margins.fit_width == FRAME[0], "a 4:3 photo is limited by the frame's width"
    assert margins.left == margins.right == 0
    assert margins.top > 0 and margins.bottom > 0


def test_margins_and_photo_add_up_to_exactly_the_frame():
    """An off-by-one here means ImagePadForOutpaint produces a canvas that
    isn't the size the sampler was set up for."""
    for source in [(4000, 3000), (1920, 1080), (1000, 1000), (900, 1601), (4032, 3024)]:
        margins = plan_margins(*source, *FRAME)

        assert margins.left + margins.fit_width + margins.right == FRAME[0]
        assert margins.top + margins.fit_height + margins.bottom == FRAME[1]


def test_the_photo_is_never_cropped_to_fit():
    """Fit-whole, not fill-and-crop: the fitted picture stays inside the
    frame and keeps its own proportions (within the latent rounding), which
    is the entire difference between this and SCENE_FIT_MODE=crop."""
    source_width, source_height = 4000, 3000
    margins = plan_margins(source_width, source_height, *FRAME)

    assert margins.fit_width <= FRAME[0]
    assert margins.fit_height <= FRAME[1]

    source_ratio = source_width / source_height
    fitted_ratio = margins.fit_width / margins.fit_height
    assert abs(fitted_ratio - source_ratio) < 0.02


def test_fitted_size_lands_on_the_latent_grid():
    margins = plan_margins(4032, 3024, *FRAME)

    assert margins.fit_width % LATENT_ALIGN == 0
    assert margins.fit_height % LATENT_ALIGN == 0


def test_a_photo_already_shaped_like_the_frame_has_no_margin():
    """Nothing to generate — and the caller uses this to skip a full
    sampling pass rather than paying for a resized copy."""
    margins = plan_margins(FRAME[0], FRAME[1], *FRAME)

    assert margins.is_empty


def test_a_portrait_taller_than_the_frame_gains_side_margins():
    margins = plan_margins(900, 2000, *FRAME)

    assert margins.top == margins.bottom == 0
    assert margins.left > 0 and margins.right > 0
    assert not margins.is_empty


def test_graph_masks_the_added_margin_and_not_the_photo():
    """VAEEncodeForInpaint must be wired to ImagePadForOutpaint's *mask*
    output (index 1), which covers only the padding. Wiring it to anything
    else would hand the sampler the pet to repaint."""
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._extend_graph(
        "photo.jpg", margins, "a cat in a cosy living room", "scene_1_bg"
    )

    pad_node = next(k for k, v in graph.items() if v["class_type"] == "ImagePadForOutpaint")
    encode = next(v for v in graph.values() if v["class_type"] == "VAEEncodeForInpaint")

    assert encode["inputs"]["mask"] == [pad_node, 1]
    assert encode["inputs"]["pixels"] == [pad_node, 0]


def test_graph_scales_the_photo_without_cropping_it():
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._extend_graph(
        "photo.jpg", margins, "a cat in a cosy living room", "scene_1_bg"
    )
    scale = next(v for v in graph.values() if v["class_type"] == "ImageScale")

    assert scale["inputs"]["crop"] == "disabled"
    assert scale["inputs"]["width"] == margins.fit_width
    assert scale["inputs"]["height"] == margins.fit_height


def test_graph_carries_the_callers_prompt_and_the_shared_negative():
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._extend_graph(
        "photo.jpg", margins, "a cat in a cosy living room", "scene_1_bg"
    )
    sampler = next(v for v in graph.values() if v["class_type"] == "KSampler")
    positive = graph[sampler["inputs"]["positive"][0]]
    negative = graph[sampler["inputs"]["negative"][0]]

    assert positive["inputs"]["text"] == "a cat in a cosy living room"
    assert negative["inputs"]["text"] == config.BACKGROUND_NEGATIVE_PROMPT


def test_the_negative_prompt_refuses_to_invent_a_second_animal():
    """A generated margin that grows another cat is a factual claim about
    the pet, not just an ugly picture (CLAUDE.md: Pet Profile 是唯一事實來源)."""
    negative = config.BACKGROUND_NEGATIVE_PROMPT.lower()

    assert "another animal" in negative
    assert "person" in negative


def test_preflight_names_the_missing_checkpoint_and_where_to_put_it(monkeypatch):
    """The checkpoint is a multi-gigabyte manual download, so "it isn't
    installed" has to say so rather than surfacing ComfyUI's validation
    error after the run has already started."""
    provider = ComfyBackgroundProvider()
    monkeypatch.setattr(provider.client, "ping", lambda: None)
    monkeypatch.setattr(provider.client, "node_options", lambda *a, **k: [])

    with pytest.raises(RuntimeError) as excinfo:
        provider.preflight()

    message = str(excinfo.value)
    assert config.BACKGROUND_MODEL_FILE in message
    assert "checkpoints" in message


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)
def test_a_frame_shaped_photo_comes_back_untouched_without_calling_the_server(tmp_path):
    photo = tmp_path / "portrait.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s={FRAME[0]}x{FRAME[1]}:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )
    assert probe_image_size(str(photo)) == FRAME

    class ExplodingProvider(ComfyBackgroundProvider):
        def preflight(self) -> None:
            raise AssertionError("must not reach the server when there is no margin")

    result = ExplodingProvider().outpaint_to_frame(
        str(photo),
        target_width=FRAME[0],
        target_height=FRAME[1],
        output_path=str(tmp_path / "out.png"),
    )

    assert result == str(photo)
    assert not (tmp_path / "out.png").exists()


def test_the_original_photo_is_composited_back_over_the_generated_canvas():
    """VAEDecode returns the whole canvas rebuilt from latents — the photo
    included, softened and re-tinted by the round-trip. The saved image must
    therefore come from the composite that puts the real pixels back, not
    straight from the decode, or "the pet is never redrawn" stops being true.
    """
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._extend_graph("photo.jpg", margins, "a cat indoors", "scene_1_bg")

    save = next(v for v in graph.values() if v["class_type"] == "SaveImage")
    composite_node = save["inputs"]["images"][0]
    composite = graph[composite_node]
    assert composite["class_type"] == "ImageCompositeMasked"

    pad_node = next(k for k, v in graph.items() if v["class_type"] == "ImagePadForOutpaint")
    decode_node = next(k for k, v in graph.items() if v["class_type"] == "VAEDecode")

    # The generated canvas underneath, the padded original photo on top.
    assert composite["inputs"]["destination"] == [decode_node, 0]
    assert composite["inputs"]["source"] == [pad_node, 0]

    # Through the inverted pad mask, i.e. exactly where the photo was.
    invert = graph[composite["inputs"]["mask"][0]]
    assert invert["class_type"] == "InvertMask"
    assert invert["inputs"]["mask"] == [pad_node, 1]


def test_the_default_prompt_is_english():
    """SDXL's text encoders are CLIP, trained on English only: a Chinese
    default would be embedded as noise and the model would invent an
    unrelated scene (measured: a night-time cityscape above the cat)."""
    assert config.BACKGROUND_DEFAULT_PROMPT.isascii()


def test_replace_generates_everything_except_the_subject():
    """The sampler must get the complement of the subject matte and the
    composite the matte itself. Swap them and the graph repaints the animal
    and keeps the old room — the exact inverse of what was asked for."""
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._replace_graph("photo.jpg", margins, "an empty park", "scene_1_bg")

    subject_node = next(k for k, v in graph.items() if v["class_type"] == "GrowMaskWithBlur")
    encode = next(v for v in graph.values() if v["class_type"] == "VAEEncodeForInpaint")
    composite = next(v for v in graph.values() if v["class_type"] == "ImageCompositeMasked")

    assert composite["inputs"]["mask"] == [subject_node, 0]

    inverted = graph[encode["inputs"]["mask"][0]]
    assert inverted["class_type"] == "InvertMask"
    assert inverted["inputs"]["mask"] == [subject_node, 0]


def test_replace_makes_the_matte_solid_before_softening_its_edge():
    """BiRefNet returns confidences, and a hazy photo comes back around 0.5
    across the whole animal — composited straight, the pet appears as a
    ghost over the generated room (measured on a real asset)."""
    provider = ComfyBackgroundProvider()
    margins = plan_margins(4000, 3000, *FRAME)

    graph = provider._replace_graph("photo.jpg", margins, "an empty park", "scene_1_bg")

    matte_node = next(k for k, v in graph.items() if v["class_type"] == "RemoveBackground")
    blur = next(v for v in graph.values() if v["class_type"] == "GrowMaskWithBlur")
    threshold = graph[blur["inputs"]["mask"][0]]

    assert threshold["class_type"] == "ThresholdMask"
    assert threshold["inputs"]["mask"] == [matte_node, 0]


def test_replace_keeps_the_subject_mask_from_swallowing_its_surroundings():
    """Expanding the subject mask keeps a halo of the *old* background around
    the animal, which is the most obvious tell that a background was
    swapped."""
    assert config.BACKGROUND_SUBJECT_GROW == 0
    assert config.BACKGROUND_SUBJECT_FEATHER > 0


def test_preflight_only_demands_matting_weights_when_replacing(monkeypatch):
    """An extend-only run never loads the matting model, so refusing to
    start without it would block work that would have succeeded."""
    provider = ComfyBackgroundProvider()
    monkeypatch.setattr(provider.client, "ping", lambda: None)
    monkeypatch.setattr(
        provider.client,
        "node_options",
        lambda class_type, _: (
            [config.BACKGROUND_MODEL_FILE] if class_type == "CheckpointLoaderSimple" else []
        ),
    )

    provider.preflight(mode="extend")

    with pytest.raises(RuntimeError, match="background-removal model"):
        provider.preflight(mode="replace")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)
def test_replace_still_has_work_to_do_on_a_frame_shaped_photo(tmp_path, monkeypatch):
    """Unlike extend, "no margin to fill" says nothing about replace — the
    whole background is still there to generate."""
    photo = tmp_path / "portrait.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s={FRAME[0]}x{FRAME[1]}:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )

    provider = ComfyBackgroundProvider()
    monkeypatch.setattr(
        provider, "preflight", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reached"))
    )

    with pytest.raises(RuntimeError, match="reached"):
        provider.replace_background(
            str(photo),
            target_width=FRAME[0],
            target_height=FRAME[1],
            output_path=str(tmp_path / "out.png"),
        )

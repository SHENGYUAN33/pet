from __future__ import annotations

import pytest

from pipeline import config
from pipeline.props import (
    PropPlacement,
    SceneProp,
    apply_props,
    region_around,
    resolve_scene_props,
)
from providers.base import ImageEditingProvider
from providers.image.comfy_background_provider import region_pixels


class FakeEditor(ImageEditingProvider):
    """Records what it was asked to paint, without a GPU in sight."""

    def __init__(self):
        self.calls: list[dict] = []

    def outpaint_to_frame(self, image_path, **kwargs):  # pragma: no cover - not exercised
        return image_path

    def replace_background(self, image_path, **kwargs):  # pragma: no cover - not exercised
        return image_path

    def add_prop(self, image_path, **kwargs):
        self.calls.append({"image_path": image_path, **kwargs})
        return kwargs["output_path"]


class BareEditor(ImageEditingProvider):
    """A provider written before props existed."""

    def outpaint_to_frame(self, image_path, **kwargs):  # pragma: no cover
        return image_path

    def replace_background(self, image_path, **kwargs):  # pragma: no cover
        return image_path


# --- interface compatibility ------------------------------------------------


def test_a_provider_without_props_still_constructs_and_refuses_clearly():
    """add_prop is concrete rather than abstract on purpose: this interface
    already has implementations, and a new required method would break every
    one of them (CLAUDE.md: Provider Adapter 介面變更需保持向下相容)."""
    provider = BareEditor()

    with pytest.raises(NotImplementedError) as excinfo:
        provider.add_prop(
            "photo.jpg", region=(0.1, 0.1, 0.5, 0.5), on_subject=True, output_path="out.png"
        )

    assert "BareEditor" in str(excinfo.value)


# --- geometry ---------------------------------------------------------------


def test_fractional_region_becomes_pixels():
    assert region_pixels((0.2, 0.3, 0.6, 0.5), 1000, 800) == (200, 240, 400, 160)


def test_a_reversed_drag_is_normalised_rather_than_erroring():
    """The fractions come from someone dragging a box; a drag that went
    right-to-left must not reach ComfyUI as a negative SolidMask."""
    assert region_pixels((0.6, 0.5, 0.2, 0.3), 1000, 800) == (200, 240, 400, 160)


def test_a_region_off_the_canvas_is_clamped():
    x, y, width, height = region_pixels((-0.5, -0.5, 2.0, 2.0), 100, 100)
    assert (x, y, width, height) == (0, 0, 100, 100)


def test_a_degenerate_region_still_has_a_pixel():
    _, _, width, height = region_pixels((0.5, 0.5, 0.5, 0.5), 100, 100)
    assert width >= 1 and height >= 1


def test_a_click_becomes_a_box_of_the_placements_own_shape():
    """A collar is a band across the body, a toy is a blob beside it — one
    default size would suit neither."""
    collar = region_around((0.5, 0.5), PropPlacement.COLLAR)
    toy = region_around((0.5, 0.5), PropPlacement.TOY)

    collar_width = collar[2] - collar[0]
    collar_height = collar[3] - collar[1]
    toy_width = toy[2] - toy[0]

    assert collar_width > collar_height, "a collar should be wider than it is tall"
    assert collar_width != toy_width


def test_a_click_at_the_edge_still_gives_a_usable_box():
    left, top, right, bottom = region_around((0.0, 1.0), PropPlacement.TOY)
    assert 0.0 <= left < right <= 1.0
    assert 0.0 <= top < bottom <= 1.0


# --- the spec ---------------------------------------------------------------


def test_a_region_outside_the_canvas_is_refused():
    with pytest.raises(ValueError):
        SceneProp(placement=PropPlacement.COLLAR, region=(0.1, 0.1, 1.4, 0.5))


def test_a_region_with_no_area_is_refused():
    with pytest.raises(ValueError):
        SceneProp(placement=PropPlacement.COLLAR, region=(0.5, 0.1, 0.5, 0.5))


def test_a_collar_paints_on_the_animal_and_a_toy_beside_it():
    """The whole reason this cannot be a plain rectangular inpaint: a collar
    intersected with the silhouette cannot spill onto the floor, and a toy
    subtracted from it cannot be painted through the cat."""
    assert PropPlacement.COLLAR.on_subject is True
    assert PropPlacement.TOY.on_subject is False


# --- who may choose a prop --------------------------------------------------


def test_a_script_may_not_choose_props_by_default():
    """A model that cannot see the photograph cannot know the animal is lying
    on its back, and a collar painted across a belly is worse than none."""
    scene = {"props": [{"placement": "collar", "region": [0.2, 0.2, 0.6, 0.4]}]}
    assert resolve_scene_props(scene) == []


def test_a_script_may_choose_props_when_that_is_switched_on(monkeypatch):
    monkeypatch.setattr(config, "PROPS_ALLOW_SCRIPT", True)
    scene = {"props": [{"placement": "collar", "region": [0.2, 0.2, 0.6, 0.4]}]}

    resolved = resolve_scene_props(scene)

    assert len(resolved) == 1
    assert resolved[0].placement is PropPlacement.COLLAR


def test_an_unusable_entry_costs_the_prop_not_the_shot(monkeypatch):
    monkeypatch.setattr(config, "PROPS_ALLOW_SCRIPT", True)
    scene = {
        "props": [
            {"placement": "hat", "region": [0.2, 0.2, 0.6, 0.4]},
            {"placement": "collar", "region": [0.2, 0.2, 0.6, 0.4]},
        ]
    }

    resolved = resolve_scene_props(scene)

    assert [p.placement for p in resolved] == [PropPlacement.COLLAR]


def test_the_reviewers_own_list_wins_over_the_script(monkeypatch):
    monkeypatch.setattr(config, "PROPS_ALLOW_SCRIPT", True)
    scene = {"props": [{"placement": "collar", "region": [0.2, 0.2, 0.6, 0.4]}]}
    override = [SceneProp(placement=PropPlacement.TOY, region=(0.1, 0.7, 0.3, 0.9))]

    assert resolve_scene_props(scene, override=override) == override


# --- applying ---------------------------------------------------------------


def test_no_props_hands_back_the_original_path():
    """Callers use the returned path and must not assume a new file exists —
    the same contract apply_background() keeps."""
    provider = FakeEditor()

    result = apply_props("photo.jpg", provider, props=[], output_path="out.png")

    assert result == "photo.jpg"
    assert provider.calls == []


def test_each_prop_paints_onto_the_previous_ones_output():
    """Two props are two objects with two descriptions; one pass asking for
    both lets the sampler put either anywhere in the union of the regions."""
    provider = FakeEditor()
    props = [
        SceneProp(placement=PropPlacement.COLLAR, region=(0.3, 0.4, 0.7, 0.5)),
        SceneProp(placement=PropPlacement.TOY, region=(0.1, 0.7, 0.3, 0.9)),
    ]

    result = apply_props("photo.jpg", provider, props=props, output_path="out.png", subject="cat")

    assert len(provider.calls) == 2
    assert provider.calls[0]["image_path"] == "photo.jpg"
    # The second pass reads the first's output, so the collar is still there
    # when the toy is painted.
    assert provider.calls[1]["image_path"] == provider.calls[0]["output_path"]
    assert result == "out.png"
    assert provider.calls[0]["on_subject"] is True
    assert provider.calls[1]["on_subject"] is False
    assert provider.calls[0]["subject"] == "cat"


def test_each_placement_brings_its_own_default_wording():
    provider = FakeEditor()
    props = [SceneProp(placement=PropPlacement.TOY, region=(0.1, 0.7, 0.3, 0.9))]

    apply_props("photo.jpg", provider, props=props, output_path="out.png")

    assert provider.calls[0]["prompt"] == config.PROPS_TOY_PROMPT


def test_an_explicit_prompt_wins_over_the_default():
    provider = FakeEditor()
    props = [
        SceneProp(
            placement=PropPlacement.COLLAR,
            region=(0.3, 0.4, 0.7, 0.5),
            prompt="a red bandana around the neck",
        )
    ]

    apply_props("photo.jpg", provider, props=props, output_path="out.png")

    assert provider.calls[0]["prompt"] == "a red bandana around the neck"


def test_intermediate_passes_leave_their_own_files():
    """When one of two props comes out wrong, which pass produced it is the
    first thing anyone needs to know."""
    provider = FakeEditor()
    props = [
        SceneProp(placement=PropPlacement.COLLAR, region=(0.3, 0.4, 0.7, 0.5)),
        SceneProp(placement=PropPlacement.TOY, region=(0.1, 0.7, 0.3, 0.9)),
    ]

    apply_props("photo.jpg", provider, props=props, output_path="out.png")

    assert provider.calls[0]["output_path"] != "out.png"
    assert provider.calls[1]["output_path"] == "out.png"

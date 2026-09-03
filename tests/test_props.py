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


# --- disclosure -------------------------------------------------------------


def test_a_propped_shot_is_disclosed_even_with_an_untouched_background():
    """A prop is the one edit here that alters the animal, so it earns the
    label on its own — not only when a background happened to be replaced."""
    from pipeline.rendering import _disclosure_notice

    notice = _disclosure_notice(
        None, [SceneProp(placement=PropPlacement.COLLAR, region=(0.3, 0.4, 0.7, 0.5))]
    )

    assert notice == config.PROPS_DISCLOSURE_TEXT


def test_a_plain_shot_carries_no_label():
    from pipeline.rendering import _disclosure_notice

    assert _disclosure_notice(None, []) is None


def test_an_extended_background_still_earns_nothing():
    """Nothing the camera saw is replaced there, and labelling a filled-in
    margin wears the label out where it matters."""
    from pipeline.background import BackgroundMode, SceneBackground
    from pipeline.rendering import _disclosure_notice

    assert _disclosure_notice(SceneBackground(mode=BackgroundMode.EXTEND), []) is None


def test_both_notices_appear_when_both_apply():
    """Picking one would drop whichever fact the other carried: the setting
    being invented and the animal wearing something it never wore are two
    different things to disclose."""
    from pipeline.background import BackgroundMode, SceneBackground
    from pipeline.rendering import _disclosure_notice

    notice = _disclosure_notice(
        SceneBackground(mode=BackgroundMode.REPLACE),
        [SceneProp(placement=PropPlacement.TOY, region=(0.1, 0.7, 0.3, 0.9))],
    )

    assert config.BACKGROUND_DISCLOSURE_TEXT in notice
    assert config.PROPS_DISCLOSURE_TEXT in notice


# --- surviving a resume -----------------------------------------------------


def test_prop_settings_round_trip_through_a_job_row():
    """Resuming has to finish the video that was being made; a shot that
    quietly lost its collar halfway through is a different video."""
    from pipeline.props import prop_specs_from_job

    original = {3: [SceneProp(placement=PropPlacement.COLLAR, region=(0.3, 0.4, 0.7, 0.5))]}
    stored = {
        str(scene_id): [prop.model_dump(mode="json") for prop in props]
        for scene_id, props in original.items()
    }

    assert prop_specs_from_job({"prop_specs": stored}) == original


def test_a_job_with_no_props_reads_back_as_none():
    from pipeline.props import prop_specs_from_job

    assert prop_specs_from_job({"prop_specs": None}) == {}
    assert prop_specs_from_job({}) == {}


def test_a_malformed_stored_entry_does_not_strand_the_resume():
    """Refusing to start because one stored region is malformed strands the
    whole run; the shot without its prop is still the shot."""
    from pipeline.props import prop_specs_from_job

    stored = {
        "2": [{"placement": "hat", "region": [0.1, 0.1, 0.4, 0.4]}],
        "3": [{"placement": "collar", "region": [0.3, 0.4, 0.7, 0.5]}],
        "nope": [{"placement": "collar", "region": [0.3, 0.4, 0.7, 0.5]}],
    }

    assert list(prop_specs_from_job({"prop_specs": stored})) == [3]


# --- forbidden words --------------------------------------------------------


def test_a_human_hand_is_refused():
    """The prompt the feature was asked for and deliberately does not
    support: a hand touching the animal claims it tolerates being handled by
    a stranger, which its Profile never said."""
    from pipeline.props import forbidden_terms_in

    found = forbidden_terms_in(
        "first-person perspective, a human hand gently holding a card towards the cat"
    )

    assert "hand" in found and "human" in found and "cat" in found


def test_another_animal_is_refused():
    """Naming one makes the model paint one, and the toy region sits exactly
    where a second cat would appear."""
    from pipeline.props import forbidden_terms_in

    assert "puppy" in forbidden_terms_in("a tiny plush mouse and another puppy beside it")


def test_ordinary_prop_wording_passes():
    from pipeline.props import forbidden_terms_in

    assert forbidden_terms_in("a soft red knitted collar, warm indoor lighting") == []


def test_matching_is_by_whole_word():
    """ "warm" must not read as "arm", and warm lighting is in the shipped
    defaults, so this is not hypothetical."""
    from pipeline.props import forbidden_terms_in

    assert forbidden_terms_in("warm delicate handmade fabric, therapy") == []


def test_the_shipped_defaults_satisfy_the_rule_they_enforce():
    """A default its own check would reject is a rule nobody can trust — and
    the first version of the collar prompt said "pet collar", which is on the
    list."""
    from pipeline.props import forbidden_terms_in

    assert forbidden_terms_in(config.PROPS_COLLAR_PROMPT) == []
    assert forbidden_terms_in(config.PROPS_TOY_PROMPT) == []


def test_the_model_itself_refuses_a_banned_prompt():
    """Enforced on the model, not only at the API boundary: a rule that lives
    in one endpoint is one the CLI and a resumed run walk straight past."""
    with pytest.raises(ValueError) as excinfo:
        SceneProp(
            placement=PropPlacement.COLLAR,
            region=(0.3, 0.4, 0.7, 0.5),
            prompt="a person's hand petting the cat",
        )

    assert "hand" in str(excinfo.value)


def test_a_script_supplied_banned_prompt_is_dropped(monkeypatch):
    monkeypatch.setattr(config, "PROPS_ALLOW_SCRIPT", True)
    scene = {
        "props": [
            {
                "placement": "collar",
                "region": [0.2, 0.2, 0.6, 0.4],
                "prompt": "a human hand adjusting it",
            }
        ]
    }

    assert resolve_scene_props(scene) == []


# --- how much of the video is dressed up ------------------------------------


def _plain_scenes(count: int) -> list[dict]:
    return [
        {"scene_id": i, "start": (i - 1) * 5, "end": i * 5, "subtitle": "x"}
        for i in range(1, count + 1)
    ]


def _collar(scene_ids) -> dict:
    return {str(i): [{"placement": "collar", "region": [0.3, 0.4, 0.7, 0.5]}] for i in scene_ids}


def test_no_props_is_not_a_finding():
    from pipeline.qa import validate_script_structure

    script = {"duration": 30, "scenes": _plain_scenes(6)}
    assert validate_script_structure(script, prop_specs={}) == []


def test_half_the_shots_dressed_is_allowed():
    from pipeline.qa import validate_script_structure

    script = {"duration": 30, "scenes": _plain_scenes(6)}
    assert validate_script_structure(script, prop_specs=_collar([1, 2, 3])) == []


def test_most_shots_dressed_is_reported():
    """An adoption video exists to show an adopter this animal; every shot
    wearing something it does not own makes it a costume shoot."""
    from pipeline.qa import validate_script_structure

    script = {"duration": 30, "scenes": _plain_scenes(6)}

    issues = validate_script_structure(script, prop_specs=_collar([1, 2, 3, 4, 5]))

    assert any("generated props" in issue for issue in issues)


def test_props_the_script_asked_for_are_counted_too(monkeypatch):
    """Counting only the reviewer's placements would let the other source
    slip past the ratio."""
    from pipeline.qa import validate_script_structure

    monkeypatch.setattr(config, "PROPS_ALLOW_SCRIPT", True)
    scenes = _plain_scenes(6)
    for scene in scenes[:5]:
        scene["props"] = [{"placement": "collar", "region": [0.3, 0.4, 0.7, 0.5]}]

    issues = validate_script_structure({"duration": 30, "scenes": scenes}, prop_specs={})

    assert any("generated props" in issue for issue in issues)


def test_a_prop_on_a_scene_that_no_longer_exists_is_not_counted():
    from pipeline.qa import validate_script_structure

    script = {"duration": 30, "scenes": _plain_scenes(6)}
    assert validate_script_structure(script, prop_specs=_collar([9, 10, 11, 12, 13])) == []

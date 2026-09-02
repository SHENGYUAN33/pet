import pytest

from pipeline.regen import apply_scene_overrides


def _sample_script() -> dict:
    return {
        "pet_id": "PET-2026-001",
        "style": "cute",
        "duration": 10,
        "scenes": [
            {
                "scene_id": 1,
                "start": 0,
                "end": 5,
                "visual_source": "IMG-001",
                "subtitle": "原本的字幕1",
                "narration": "原本的旁白1",
            },
            {
                "scene_id": 2,
                "start": 5,
                "end": 10,
                "visual_source": "IMG-002",
                "subtitle": "原本的字幕2",
                "narration": "原本的旁白2",
            },
        ],
    }


def test_overrides_only_the_targeted_scene():
    script = _sample_script()

    patched = apply_scene_overrides(script, 1, subtitle="新字幕")

    assert patched["scenes"][0]["subtitle"] == "新字幕"
    assert patched["scenes"][1]["subtitle"] == "原本的字幕2"


def test_overrides_multiple_fields_at_once():
    script = _sample_script()

    patched = apply_scene_overrides(
        script, 2, visual_source="IMG-005", subtitle="新字幕2", narration="新旁白2"
    )

    scene = patched["scenes"][1]
    assert scene["visual_source"] == "IMG-005"
    assert scene["subtitle"] == "新字幕2"
    assert scene["narration"] == "新旁白2"


def test_fields_left_as_none_are_unchanged():
    script = _sample_script()

    patched = apply_scene_overrides(script, 1, visual_source="IMG-999")

    scene = patched["scenes"][0]
    assert scene["visual_source"] == "IMG-999"
    assert scene["subtitle"] == "原本的字幕1"
    assert scene["narration"] == "原本的旁白1"


def test_unknown_scene_id_raises():
    script = _sample_script()

    with pytest.raises(ValueError, match="scene_id 99"):
        apply_scene_overrides(script, 99, subtitle="x")


def test_does_not_mutate_the_original_script():
    script = _sample_script()

    apply_scene_overrides(script, 1, subtitle="新字幕")

    assert script["scenes"][0]["subtitle"] == "原本的字幕1"


def test_overlay_override_replaces_the_scenes_panel():
    from pipeline.overlay_renderer import OverlayTemplate, SceneOverlaySpec

    script = _sample_script()
    script["scenes"][0]["overlay"] = {"template": "center_quote", "headline": "舊的大字"}

    patched = apply_scene_overrides(
        script,
        1,
        overlay=SceneOverlaySpec(template=OverlayTemplate.SPEECH_BUBBLE, quote="喜歡呼嚕嚕"),
    )

    assert patched["scenes"][0]["overlay"]["template"] == "speech_bubble"
    assert patched["scenes"][0]["overlay"]["quote"] == "喜歡呼嚕嚕"
    # Wholesale, not merged: copy belonging to the template the reviewer
    # switched away from would sit on the scene unrendered but still read by
    # fact-checking.
    assert patched["scenes"][0]["overlay"]["headline"] is None
    # The original is untouched, like every other override here.
    assert script["scenes"][0]["overlay"]["headline"] == "舊的大字"


def test_overlay_template_none_takes_the_panel_off():
    """A reviewer looking at a panel that does not work has to be able to
    remove it, which is why this is one nullable object rather than a set of
    nullable strings — those can only ever mean "leave it alone"."""
    from pipeline.overlay_renderer import OverlayTemplate, SceneOverlaySpec, resolve_scene_overlay

    script = _sample_script()
    script["scenes"][1]["overlay"] = {"template": "info_sidebar", "tags": ["年齡：2歲"]}

    patched = apply_scene_overrides(
        script, 2, overlay=SceneOverlaySpec(template=OverlayTemplate.NONE)
    )

    assert resolve_scene_overlay(patched["scenes"][1]) is None


def test_leaving_overlay_out_keeps_what_the_script_chose():
    script = _sample_script()
    script["scenes"][0]["overlay"] = {"template": "center_quote", "headline": "腳本寫的"}

    patched = apply_scene_overrides(script, 1, subtitle="只改字幕")

    assert patched["scenes"][0]["overlay"]["headline"] == "腳本寫的"
    assert patched["scenes"][0]["subtitle"] == "只改字幕"

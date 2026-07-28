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

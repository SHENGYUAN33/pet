import json

import pytest

from pipeline.profile import PetProfile
from pipeline.script_gen import SCRIPT_STYLES, _build_prompt, generate_script
from providers.base import LLMProvider

EXAMPLE_PROFILE = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "docs"
    / "schemas"
    / "pet_profile.example.json"
)


class FakeLLM(LLMProvider):
    """Deterministic stub so script_gen can be tested without a running
    Ollama server."""

    def complete(self, prompt: str) -> str:
        return json.dumps(
            {
                "pet_id": "PET-2026-001",
                "title": "豆豆正在等一個家",
                "style": "cute",
                "duration": 30,
                "language": "zh-TW",
                "scenes": [
                    {
                        "scene_id": 1,
                        "start": 0,
                        "end": 3,
                        "purpose": "hook",
                        "visual_source": "img-001.jpg",
                        "narration": "嗨，先別滑走，我等你好久了！",
                        "subtitle": "先別滑走！我在等你",
                    },
                    {
                        "scene_id": 2,
                        "start": 3,
                        "end": 30,
                        "purpose": "intro",
                        "visual_source": "vid-001.mp4",
                        "narration": "我不親貓，但我超級親人。",
                        "subtitle": "不親貓，但很親人",
                    },
                ],
                "cta": "點擊查看豆豆的領養資訊",
                "cta_url": "https://example.org/adopt/PET-2026-001",
            },
            ensure_ascii=False,
        )


def test_generate_script_rejects_unknown_style():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    try:
        generate_script(profile, FakeLLM(), style="not_a_style")
    except ValueError as e:
        assert "not_a_style" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown style")


def test_generate_script_parses_llm_json():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = generate_script(profile, FakeLLM(), style="cute")
    assert script["pet_id"] == profile.pet_id
    assert len(script["scenes"]) == 2
    assert script["scenes"][0]["purpose"] == "hook"


def test_script_styles_constant():
    assert SCRIPT_STYLES == ["cute", "warm_story", "contrast_humor"]


def test_prompt_includes_required_disclosure_restrictions():
    """docs/architecture.md §4: 必要照護限制不得因廣告目的而被隱藏. The LLM can
    only honor that if the prompt actually states the restrictions and marks
    them as mandatory — this only checks the prompt we send, not whether a
    live model actually complies (that needs a real-LLM run, see the manual
    verification noted in CLAUDE.md)."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    assert profile.personality_tags.restrictions, (
        "fixture profile must have restrictions to test this"
    )

    prompt = _build_prompt(profile, style="cute", duration=30)

    for restriction in profile.personality_tags.restrictions:
        assert restriction in prompt
    assert "必要揭露（不可省略）" in prompt


def test_prompt_shows_placeholder_when_profile_has_no_restrictions():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    profile.personality_tags.restrictions = []

    prompt = _build_prompt(profile, style="cute", duration=30)

    assert "必要揭露（不可省略）：\n（無）" in prompt


def test_prompt_generation_refuses_a_profile_with_no_media_assets():
    """The renderer can only use assets the profile lists, so a pet without
    any has to be rejected before three LLM calls and a TTS pass are spent
    on a script whose visual_source values cannot resolve."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    profile.media.assets = []

    with pytest.raises(ValueError, match="no media assets"):
        generate_script(profile, FakeLLM(), style="cute")

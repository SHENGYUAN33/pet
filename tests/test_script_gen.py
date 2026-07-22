import json

from pipeline.profile import PetProfile
from pipeline.script_gen import SCRIPT_STYLES, generate_script
from providers.base import LLMProvider

EXAMPLE_PROFILE = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "docs" / "schemas" / "pet_profile.example.json"
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

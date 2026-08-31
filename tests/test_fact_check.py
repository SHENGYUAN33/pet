from pathlib import Path

from pipeline import config
from pipeline.fact_check import find_missing_disclosures, find_unsupported_claims
from pipeline.profile import PetProfile
from providers.base import LLMProvider

EXAMPLE_PROFILE = (
    Path(__file__).resolve().parent.parent / "docs" / "schemas" / "pet_profile.example.json"
)


def _script_with_scenes(*narrations_and_subtitles: tuple[str, str]) -> dict:
    return {
        "scenes": [
            {"narration": narration, "subtitle": subtitle}
            for narration, subtitle in narrations_and_subtitles
        ]
    }


def test_flags_restriction_missing_from_every_scene():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    assert profile.personality_tags.restrictions == ["不親貓", "不建議與小型動物共居"]

    script = _script_with_scenes(
        ("嗨，我是豆豆！", "先別滑走"),
        ("我最愛玩球了", "超愛撒嬌"),
    )

    missing = find_missing_disclosures(script, profile)

    assert missing == ["不親貓", "不建議與小型動物共居"]


def test_passes_when_every_restriction_appears_somewhere():
    profile = PetProfile.load(EXAMPLE_PROFILE)

    script = _script_with_scenes(
        ("嗨，我是豆豆！", "先別滑走"),
        ("我不親貓，但很親人", "不建議與小型動物共居喔"),
    )

    assert find_missing_disclosures(script, profile) == []


def test_partial_disclosure_reports_only_the_missing_one():
    """Regression case for the exact failure found during manual testing:
    a style disclosed the diet restriction but silently dropped the
    no-other-cats restriction."""
    profile = PetProfile.load(EXAMPLE_PROFILE)

    script = _script_with_scenes(
        ("嗨，我是豆豆！", "先別滑走"),
        ("我不建議與小型動物共居", "詳情點擊了解我"),
    )

    assert find_missing_disclosures(script, profile) == ["不親貓"]


def test_no_restrictions_means_nothing_to_flag():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    profile.personality_tags.restrictions = []

    script = _script_with_scenes(("嗨！", "來看我"))

    assert find_missing_disclosures(script, profile) == []


class FakeLLM(LLMProvider):
    """Answers with whatever a test hands it, in place of a 7B model."""

    def __init__(self, answer: str = "{}"):
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


def test_a_paraphrased_disclosure_is_cleared_by_the_model():
    """The exact false positive the old substring-only check was documented
    to produce: a restriction disclosed in slightly different words was
    reported missing, which teaches reviewers to ignore the warning."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = _script_with_scenes(("我不太親近其他貓咪喔", "不親貓咪"))
    llm = FakeLLM('{"disclosed": ["不親貓"], "missing": ["不建議與小型動物共居"]}')

    missing = find_missing_disclosures(script, profile, llm)

    assert missing == ["不建議與小型動物共居"]


def test_a_restriction_stated_word_for_word_never_reaches_the_model():
    """Certain, free, and covers most of them — the model is only there for
    the wording the substring check cannot match."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = _script_with_scenes(("我不親貓，也不建議與小型動物共居", "請注意"))
    llm = FakeLLM()

    assert find_missing_disclosures(script, profile, llm) == []
    assert llm.prompts == []


def test_the_substring_verdict_stands_when_the_model_cannot_answer():
    """The model is only ever asked to clear a restriction, never to condemn
    one, so losing it means falling back to "ask a human" rather than to
    silence."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = _script_with_scenes(("我不太親近其他貓咪喔", ""))

    class Unreachable(LLMProvider):
        def complete(self, prompt: str) -> str:
            raise ConnectionError("Ollama is not running")

    missing = find_missing_disclosures(script, profile, Unreachable())

    assert missing == profile.personality_tags.restrictions


def test_without_a_model_the_old_exact_matching_is_what_happens():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = _script_with_scenes(("我不太親近其他貓咪喔", ""))

    assert find_missing_disclosures(script, profile) == profile.personality_tags.restrictions


def test_an_invented_claim_is_reported():
    """The half of fact-checking that had no check at all. A video that
    invents "最愛跟小孩玩" is how an adopter takes home an animal nobody
    described to them."""
    profile = PetProfile.load(EXAMPLE_PROFILE)
    script = _script_with_scenes(("我最愛跟小孩一起玩！", "很親小孩"))
    llm = FakeLLM('{"unsupported": [{"quote": "我最愛跟小孩一起玩！", "why": "資料沒有提到小孩"}]}')

    claims = find_unsupported_claims(script, profile, llm)

    assert len(claims) == 1
    assert "我最愛跟小孩一起玩！" in claims[0]
    assert "資料沒有提到小孩" in claims[0]


def test_nothing_invented_reports_nothing():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    llm = FakeLLM('{"unsupported": []}')

    assert find_unsupported_claims(_script_with_scenes(("我叫豆豆", "")), profile, llm) == []


def test_invented_claims_cannot_be_found_without_a_model():
    """No amount of string matching tells a fabricated sentence from a true
    one, so without a model this reports nothing rather than guessing."""
    profile = PetProfile.load(EXAMPLE_PROFILE)

    assert find_unsupported_claims(_script_with_scenes(("我會開車", "")), profile) == []


def test_an_unreachable_model_does_not_invent_findings(monkeypatch):
    profile = PetProfile.load(EXAMPLE_PROFILE)

    class Unreachable(LLMProvider):
        def complete(self, prompt: str) -> str:
            raise ConnectionError("Ollama is not running")

    assert (
        find_unsupported_claims(_script_with_scenes(("我會開車", "")), profile, Unreachable()) == []
    )


def test_the_semantic_checks_can_be_switched_off(monkeypatch):
    """Falling back to exact matching costs a class of false positive and
    the invented-claim check entirely — worth being able to choose, worth
    saying so."""
    monkeypatch.setattr(config, "FACT_CHECK_SEMANTIC_ENABLED", False)
    profile = PetProfile.load(EXAMPLE_PROFILE)
    llm = FakeLLM('{"disclosed": ["不親貓"]}')
    script = _script_with_scenes(("我不太親近其他貓咪喔", ""))

    assert find_missing_disclosures(script, profile, llm) == profile.personality_tags.restrictions
    assert find_unsupported_claims(script, profile, llm) == []
    assert llm.prompts == []

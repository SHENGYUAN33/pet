from pathlib import Path

from pipeline.fact_check import find_missing_disclosures
from pipeline.profile import PetProfile

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

"""The closing recap of assets the script had no room for.

The complaint it answers: someone uploads thirteen photos, gets a 30-second
video, and six of the photos never appear. The story shots stay as they are
(one line of narration each) and the leftovers get a quick cut at the end.
"""

from __future__ import annotations

from pipeline import config
from pipeline.montage import (
    RECAP_PURPOSE,
    append_recap_scene,
    recap_asset_duration,
    unused_assets,
)
from pipeline.profile import PetProfile


def _profile(asset_count: int) -> PetProfile:
    return PetProfile.model_validate(
        {
            "pet_id": "PET-TEST-RECAP",
            "name": "測試貓",
            "species": "cat",
            "sex": "male",
            "age": "1歲",
            "size": "medium",
            "health_status": {"vaccinated": True, "neutered": True, "microchipped": True},
            "personality_tags": {
                "appeal": [],
                "lifestyle_fit": [],
                "care_needs": [],
                "restrictions": [],
            },
            "adoption_requirements": [],
            "contact_url": "https://example.org/adopt/test",
            "media": {
                "assets": [
                    {"asset_id": f"photo_{i:02d}", "type": "photo", "url": f"p{i}.jpg"}
                    for i in range(1, asset_count + 1)
                ]
            },
            "identity_card": {},
        }
    )


def _script(used_sources: list[str]) -> dict:
    return {
        "pet_id": "PET-TEST-RECAP",
        "style": "cute",
        "duration": len(used_sources) * 5,
        "cta": "點我看領養資訊",
        "scenes": [
            {
                "scene_id": i,
                "start": (i - 1) * 5,
                "end": i * 5,
                "visual_source": source,
                "subtitle": f"字幕{i}",
                "narration": f"旁白{i}",
            }
            for i, source in enumerate(used_sources, start=1)
        ],
    }


def test_unused_assets_are_the_ones_no_scene_references():
    profile = _profile(13)
    script = _script(["photo_01", "photo_05", "photo_09"])

    leftovers = [a.asset_id for a in unused_assets(profile, script)]

    assert "photo_01" not in leftovers
    assert "photo_02" in leftovers
    assert len(leftovers) == 10


def test_unused_assets_matches_on_filename_too():
    """The script prompt offers both asset_id and filename, and rendering
    resolves either — so a scene naming the file counts as using it."""
    profile = _profile(3)
    script = _script(["p2.jpg"])

    assert [a.asset_id for a in unused_assets(profile, script)] == ["photo_01", "photo_03"]


def test_recap_appends_one_shot_carrying_every_leftover():
    profile = _profile(13)
    script = _script([f"photo_{i:02d}" for i in range(1, 8)])

    updated = append_recap_scene(script, profile)
    recap = updated["scenes"][-1]

    assert len(updated["scenes"]) == len(script["scenes"]) + 1
    assert recap["purpose"] == RECAP_PURPOSE
    assert recap["visual_sources"] == [f"photo_{i:02d}" for i in range(8, 14)]
    assert recap["subtitle"] == script["cta"], "the CTA is what should be readable"
    assert not recap["narration"], "the story has already finished"


def test_recap_starts_where_the_story_ends_and_extends_the_duration():
    profile = _profile(9)
    script = _script([f"photo_{i:02d}" for i in range(1, 8)])

    updated = append_recap_scene(script, profile)
    recap = updated["scenes"][-1]

    assert recap["start"] == script["scenes"][-1]["end"]
    assert recap["end"] > recap["start"]
    assert updated["duration"] == recap["end"]


def test_recap_does_not_mutate_the_script_it_was_given():
    profile = _profile(9)
    script = _script([f"photo_{i:02d}" for i in range(1, 8)])

    append_recap_scene(script, profile)

    assert len(script["scenes"]) == 7


def test_no_recap_when_every_asset_is_already_on_screen():
    profile = _profile(5)
    script = _script([f"photo_{i:02d}" for i in range(1, 6)])

    assert append_recap_scene(script, profile) is script


def test_per_asset_time_shrinks_as_leftovers_pile_up_but_never_drops_them():
    """Twenty leftovers must still all appear — the recap runs longer rather
    than cutting any of them."""
    few = recap_asset_duration(3)
    many = recap_asset_duration(20)

    assert few == config.RECAP_ASSET_DURATION
    assert many < few
    assert many >= config.RECAP_MIN_ASSET_DURATION

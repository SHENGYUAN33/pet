from pathlib import Path

from pipeline.profile import PetProfile

EXAMPLE_PROFILE = Path(__file__).resolve().parent.parent / "docs" / "schemas" / "pet_profile.example.json"


def test_loads_example_profile():
    profile = PetProfile.load(EXAMPLE_PROFILE)
    assert profile.pet_id == "PET-2026-001"
    assert profile.name == "豆豆"
    assert profile.health_status.neutered is True
    assert "不親貓" in profile.personality_tags.restrictions
    assert len(profile.media.assets) == 2

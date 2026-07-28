from __future__ import annotations

import pytest

from pipeline import db, pet_repo
from pipeline.models import Pet
from pipeline.profile import PetProfile

TEST_PET_ID = "PET-TEST-REPO-0001"


def _db_reachable() -> bool:
    try:
        with db.engine.connect():
            pass
        return True
    except Exception:  # noqa: BLE001 - any failure here just means "skip these tests"
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="PostgreSQL not reachable at DATABASE_URL"
)


def _sample_profile(pet_id: str = TEST_PET_ID, name: str = "測試貓") -> PetProfile:
    return PetProfile.model_validate(
        {
            "pet_id": pet_id,
            "name": name,
            "species": "cat",
            "sex": "male",
            "age": "1歲",
            "size": "medium",
            "health_status": {"vaccinated": True, "neutered": True, "microchipped": True},
            "personality_tags": {
                "appeal": ["活潑"],
                "lifestyle_fit": [],
                "care_needs": [],
                "restrictions": ["不親貓"],
            },
            "adoption_requirements": [],
            "contact_url": "https://example.org/adopt/test",
            "media": {"assets": []},
            "identity_card": {},
        }
    )


def _delete_test_pet() -> None:
    with db.get_session() as session:
        row = session.get(Pet, TEST_PET_ID)
        if row is not None:
            session.delete(row)


@pytest.fixture(autouse=True)
def _clean_test_pet():
    db.init_db()
    _delete_test_pet()
    yield
    _delete_test_pet()


def test_save_and_get_pet_roundtrip():
    pet_repo.save_pet(_sample_profile())

    loaded = pet_repo.get_pet(TEST_PET_ID)

    assert loaded is not None
    assert loaded.pet_id == TEST_PET_ID
    assert loaded.name == "測試貓"
    assert loaded.personality_tags.restrictions == ["不親貓"]


def test_get_pet_returns_none_for_unknown_id():
    assert pet_repo.get_pet("PET-DOES-NOT-EXIST") is None


def test_save_pet_upserts_existing():
    pet_repo.save_pet(_sample_profile(name="測試貓"))
    pet_repo.save_pet(_sample_profile(name="測試貓改名了"))

    loaded = pet_repo.get_pet(TEST_PET_ID)

    assert loaded is not None
    assert loaded.name == "測試貓改名了"


def test_list_pets_includes_saved_pet():
    pet_repo.save_pet(_sample_profile())

    pet_ids = [p.pet_id for p in pet_repo.list_pets()]

    assert TEST_PET_ID in pet_ids


def _sample_script() -> dict:
    return {
        "scenes": [
            {"scene_id": 1, "start": 0, "end": 5, "visual_source": "IMG-001", "subtitle": "a"},
            {"scene_id": 2, "start": 5, "end": 10, "visual_source": "IMG-002", "subtitle": "b"},
        ]
    }


def test_record_and_list_generation_jobs():
    pet_repo.save_pet(_sample_profile())

    job_id = pet_repo.record_generation_job(
        TEST_PET_ID,
        style="cute",
        duration=30,
        output_path="storage/output/PET-TEST-REPO-0001/out.mp4",
        disclosure_missing=[],
        structure_issues=["scene 2 has an empty subtitle"],
        script_json=_sample_script(),
    )

    jobs = pet_repo.list_generation_jobs(TEST_PET_ID)

    assert isinstance(job_id, int)
    assert len(jobs) == 1
    assert jobs[0]["style"] == "cute"
    assert jobs[0]["structure_issues"]["issues"] == ["scene 2 has an empty subtitle"]
    assert jobs[0]["scene_count"] == 2
    assert jobs[0]["parent_job_id"] is None


def test_get_generation_job_returns_full_script_and_regeneration_links_parent():
    pet_repo.save_pet(_sample_profile())

    job_id = pet_repo.record_generation_job(
        TEST_PET_ID,
        style="cute",
        duration=30,
        output_path="storage/output/PET-TEST-REPO-0001/out.mp4",
        disclosure_missing=[],
        structure_issues=[],
        script_json=_sample_script(),
    )

    fetched = pet_repo.get_generation_job(job_id)
    assert fetched is not None
    assert fetched["script_json"]["scenes"][0]["visual_source"] == "IMG-001"
    assert fetched["parent_job_id"] is None

    revision_id = pet_repo.record_generation_job(
        TEST_PET_ID,
        style="cute",
        duration=30,
        output_path="storage/output/PET-TEST-REPO-0001/out2.mp4",
        disclosure_missing=[],
        structure_issues=[],
        script_json=_sample_script(),
        parent_job_id=job_id,
    )

    revision = pet_repo.get_generation_job(revision_id)
    assert revision is not None
    assert revision["parent_job_id"] == job_id


def test_get_generation_job_returns_none_for_unknown_id():
    assert pet_repo.get_generation_job(999_999_999) is None

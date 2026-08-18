from __future__ import annotations

import json

import pytest

from pipeline import config, db
from pipeline.models import Pet

pytest.importorskip("fastapi")

TEST_PET_ID = "PET-TEST-WEBAPP-0001"


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


def _delete_test_pet() -> None:
    with db.get_session() as session:
        row = session.get(Pet, TEST_PET_ID)
        if row is not None:
            session.delete(row)


@pytest.fixture(autouse=True)
def _clean_test_pet():
    _delete_test_pet()
    yield
    _delete_test_pet()


def _sample_profile_json(pet_id: str = TEST_PET_ID, name: str = "測試貓") -> dict:
    return {
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
            "restrictions": [],
        },
        "adoption_requirements": [],
        "contact_url": "https://example.org/adopt/test",
        "media": {"assets": []},
        "identity_card": {},
    }


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from webapp.main import app

    return TestClient(app)


def test_list_pets_returns_200(client):
    response = client.get("/api/pets")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_unknown_pet_returns_404(client):
    response = client.get("/api/pets/PET-DOES-NOT-EXIST")
    assert response.status_code == 404


def test_get_unknown_job_video_returns_404(client):
    response = client.get("/api/jobs/999999999/video")
    assert response.status_code == 404


def test_regenerate_scene_on_unknown_job_returns_404(client):
    response = client.post(
        "/api/jobs/999999999/regenerate-scene",
        json={"scene_id": 1, "subtitle": "test"},
    )
    assert response.status_code == 404


def test_save_profile_creates_new_pet(client):
    response = client.put(
        f"/api/pets/{TEST_PET_ID}/profile",
        json=_sample_profile_json(),
    )
    assert response.status_code == 200
    assert response.json() == {"pet_id": TEST_PET_ID, "name": "測試貓"}

    fetched = client.get(f"/api/pets/{TEST_PET_ID}")
    assert fetched.status_code == 200
    assert fetched.json()["profile"]["name"] == "測試貓"


def test_save_profile_upserts_existing_pet(client):
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json(name="測試貓"))

    response = client.put(
        f"/api/pets/{TEST_PET_ID}/profile",
        json=_sample_profile_json(name="測試貓改名了"),
    )

    assert response.status_code == 200
    fetched = client.get(f"/api/pets/{TEST_PET_ID}")
    assert fetched.json()["profile"]["name"] == "測試貓改名了"


def test_save_profile_rejects_pet_id_mismatch(client):
    response = client.put(
        "/api/pets/PET-DIFFERENT-ID/profile",
        json=_sample_profile_json(),
    )
    assert response.status_code == 400


def test_save_profile_rejects_invalid_profile(client):
    response = client.put(
        f"/api/pets/{TEST_PET_ID}/profile",
        json={"pet_id": TEST_PET_ID, "name": "缺欄位"},
    )
    assert response.status_code == 422


def test_import_path_rejects_traversal_outside_profiles_dir(client, tmp_path):
    outside_file = tmp_path / "not_a_profile.json"
    outside_file.write_text(json.dumps(_sample_profile_json()), encoding="utf-8")

    response = client.post("/api/pets/import-path", json={"path": str(outside_file)})

    assert response.status_code == 400


def test_import_path_returns_404_for_missing_file(client):
    response = client.post(
        "/api/pets/import-path", json={"path": "PET-DOES-NOT-EXIST-ON-DISK.json"}
    )
    assert response.status_code == 404


def test_import_path_loads_and_saves_pet(client):
    profile_path = config.PROFILES_DIR / f"{TEST_PET_ID}.json"
    profile_path.write_text(
        json.dumps(_sample_profile_json(), ensure_ascii=False), encoding="utf-8"
    )
    try:
        response = client.post("/api/pets/import-path", json={"path": f"{TEST_PET_ID}.json"})
        assert response.status_code == 200
        assert response.json() == {"pet_id": TEST_PET_ID, "name": "測試貓"}

        fetched = client.get(f"/api/pets/{TEST_PET_ID}")
        assert fetched.status_code == 200
    finally:
        profile_path.unlink(missing_ok=True)


def test_get_unknown_job_returns_404(client):
    response = client.get("/api/jobs/999999999")
    assert response.status_code == 404


def test_get_job_returns_script_scenes(client):
    """The review UI lists a job's scenes so the reviewer can pick one to
    regenerate instead of typing a raw scene id."""
    from pipeline.pet_repo import record_generation_job, save_pet
    from pipeline.profile import PetProfile

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    script = {
        "pet_id": TEST_PET_ID,
        "scenes": [
            {"scene_id": 1, "purpose": "hook", "subtitle": "先別滑走", "narration": "嗨"},
            {"scene_id": 2, "purpose": "intro", "subtitle": "我是測試貓", "narration": "你好"},
        ],
    }
    job_id = record_generation_job(
        TEST_PET_ID,
        style="cute",
        duration=30,
        output_path="storage/output/does-not-need-to-exist.mp4",
        disclosure_missing=[],
        structure_issues=[],
        script_json=script,
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job_id
    assert [s["scene_id"] for s in body["script_json"]["scenes"]] == [1, 2]

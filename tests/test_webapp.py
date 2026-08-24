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
    from pipeline.pet_repo import save_pet
    from pipeline.profile import PetProfile
    from tests.conftest import completed_job

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    script = {
        "pet_id": TEST_PET_ID,
        "scenes": [
            {"scene_id": 1, "purpose": "hook", "subtitle": "先別滑走", "narration": "嗨"},
            {"scene_id": 2, "purpose": "intro", "subtitle": "我是測試貓", "narration": "你好"},
        ],
    }
    job_id = completed_job(TEST_PET_ID, script_json=script)

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job_id
    assert [s["scene_id"] for s in body["script_json"]["scenes"]] == [1, 2]


def _asset_dir():
    return config.ASSETS_DIR / TEST_PET_ID


@pytest.fixture
def _clean_test_assets():
    yield
    directory = _asset_dir()
    if directory.is_dir():
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()


def test_upload_asset_stores_file_and_returns_relative_path(client, _clean_test_assets):
    """Uploading is how the reviewer's OS file dialog reaches the pipeline —
    the browser never exposes the picked file's real local path."""
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())

    response = client.post(
        f"/api/pets/{TEST_PET_ID}/assets",
        files={"file": ("元寶 照片.JPG", b"not-really-a-jpeg", "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "photo"
    assert body["path"] == f"storage/assets/{TEST_PET_ID}/{body['filename']}"
    assert (config.BASE_DIR / body["path"]).is_file()

    listed = client.get(f"/api/pets/{TEST_PET_ID}/assets")
    assert [a["filename"] for a in listed.json()] == [body["filename"]]


def test_upload_asset_does_not_overwrite_same_name(client, _clean_test_assets):
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())

    first = client.post(
        f"/api/pets/{TEST_PET_ID}/assets", files={"file": ("photo.png", b"a", "image/png")}
    ).json()
    second = client.post(
        f"/api/pets/{TEST_PET_ID}/assets", files={"file": ("photo.png", b"b", "image/png")}
    ).json()

    assert first["filename"] != second["filename"]
    assert (config.BASE_DIR / first["path"]).read_bytes() == b"a"


def test_upload_asset_rejects_unsupported_extension(client, _clean_test_assets):
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())

    response = client.post(
        f"/api/pets/{TEST_PET_ID}/assets",
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert not (_asset_dir() / "payload.exe").exists()


def test_upload_asset_rejects_unknown_pet(client):
    response = client.post(
        "/api/pets/PET-DOES-NOT-EXIST/assets",
        files={"file": ("photo.png", b"a", "image/png")},
    )
    assert response.status_code == 404


def test_asset_dir_rejects_path_traversal_pet_id():
    """pet_id is used as a directory component, so it must never be able to
    address anything outside storage/assets/."""
    from fastapi import HTTPException

    from webapp.main import _pet_asset_dir

    for bad_id in ["../evil", "..", "PET/../../evil", r"C:\Windows"]:
        with pytest.raises(HTTPException) as excinfo:
            _pet_asset_dir(bad_id, create=True)
        assert excinfo.value.status_code == 400


def test_list_profile_files_returns_json_filenames(client):
    profile_path = config.PROFILES_DIR / f"{TEST_PET_ID}.json"
    profile_path.write_text(
        json.dumps(_sample_profile_json(), ensure_ascii=False), encoding="utf-8"
    )
    try:
        response = client.get("/api/profile-files")
        assert response.status_code == 200
        assert f"{TEST_PET_ID}.json" in response.json()
    finally:
        profile_path.unlink(missing_ok=True)


def test_generate_on_unknown_pet_returns_404_without_starting_a_task(client):
    """Bad input is rejected on the request, not surfaced a minute later as a
    failed background task."""
    from webapp import tasks

    response = client.post("/api/pets/PET-DOES-NOT-EXIST/generate", json={"style": "cute"})

    assert response.status_code == 404
    assert tasks.running_task() is None


def test_get_unknown_task_returns_404(client):
    assert client.get("/api/tasks/nope").status_code == 404


def test_generate_while_another_task_runs_returns_409(client):
    """The UI needs a clear 'already busy' answer instead of two runs fighting
    over the GPU."""
    import threading

    from webapp import tasks

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    release = threading.Event()
    tasks.start_task(
        kind="generate",
        pet_id="PET-OTHER",
        label="正在跑的工作",
        work=lambda on_progress: release.wait(timeout=5) and {},
    )
    try:
        response = client.post(f"/api/pets/{TEST_PET_ID}/generate", json={"style": "cute"})
        assert response.status_code == 409
        assert "正在跑的工作" in response.json()["detail"]
    finally:
        release.set()
        with tasks._lock:
            tasks._tasks.clear()


def test_finished_task_whose_record_vanished_does_not_kill_the_thread():
    """A task record can disappear while its thread still runs (shutdown, or a
    test clearing state). The thread must finish quietly instead of dying with
    an unhandled KeyError."""
    import threading

    from webapp import tasks

    started = threading.Event()
    release = threading.Event()

    def work(on_progress):
        started.set()
        release.wait(timeout=5)
        return {}

    task = tasks.start_task(kind="generate", pet_id="PET-GONE", label="消失的工作", work=work)
    thread = next(t for t in threading.enumerate() if t.name == f"task-{task['task_id']}")
    assert started.wait(timeout=5)

    with tasks._lock:
        tasks._tasks.clear()
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert tasks.get_task(task["task_id"]) is None


def test_video_of_an_unfinished_job_returns_409_not_500(client):
    """A job row now exists while the run is still going, so the video
    endpoint has to cope with a job that has no output file yet."""
    from pipeline.pet_repo import save_pet, start_generation_job
    from pipeline.profile import PetProfile

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    job_id = start_generation_job(TEST_PET_ID, style="cute", duration=30)

    response = client.get(f"/api/jobs/{job_id}/video")

    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_resume_endpoint_rejects_a_finished_job(client):
    """Resuming a job that already produced its video would re-render it for
    nothing."""
    from pipeline.pet_repo import save_pet
    from pipeline.profile import PetProfile
    from tests.conftest import completed_job

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": []})

    response = client.post(f"/api/jobs/{job_id}/resume")

    assert response.status_code == 409
    assert "已經完成" in response.json()["detail"]


def test_resume_endpoint_rejects_a_job_with_no_script(client):
    from pipeline.pet_repo import fail_generation_job, save_pet, start_generation_job
    from pipeline.profile import PetProfile

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    job_id = start_generation_job(TEST_PET_ID, style="cute", duration=30)
    fail_generation_job(job_id, "died during script generation")

    response = client.post(f"/api/jobs/{job_id}/resume")

    assert response.status_code == 409
    assert "腳本產生前" in response.json()["detail"]


def test_job_detail_includes_per_scene_rows(client):
    """The review UI shows which shot failed and what made each one."""
    from pipeline.pet_repo import save_pet, start_scene_job
    from pipeline.profile import PetProfile
    from tests.conftest import completed_job

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": []})
    start_scene_job(
        job_id, 1, visual_source="IMG-001", video_provider="wan", animate_prompt="貓輕輕搖尾巴"
    )

    body = client.get(f"/api/jobs/{job_id}").json()

    assert len(body["scene_jobs"]) == 1
    assert body["scene_jobs"][0]["video_provider"] == "wan"
    assert body["scene_jobs"][0]["animate_prompt"] == "貓輕輕搖尾巴"


def test_startup_reaps_interrupted_jobs():
    """Running the app's lifespan is what closes out jobs the previous
    process died holding."""
    from fastapi.testclient import TestClient

    from pipeline.pet_repo import get_generation_job, save_pet, start_generation_job
    from pipeline.profile import PetProfile
    from webapp.main import app

    save_pet(PetProfile.model_validate(_sample_profile_json()))
    job_id = start_generation_job(TEST_PET_ID, style="cute", duration=30)

    with TestClient(app):  # entering the context manager runs lifespan
        pass

    job = get_generation_job(job_id)
    assert job["status"] == "failed"
    assert "重新啟動" in job["error"]

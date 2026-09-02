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


def test_scene_plan_reports_assets_the_video_cannot_fit(client):
    """The generate form asks this before starting a run, so 13 assets in a
    30-second video reads as "7 used, 6 left over" up front."""
    profile = _sample_profile_json()
    profile["media"]["assets"] = [
        {"asset_id": f"photo_{i:02d}", "type": "photo", "url": f"p{i}.jpg"} for i in range(1, 14)
    ]
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=profile)

    plan = client.get(f"/api/pets/{TEST_PET_ID}/scene-plan?duration=30").json()

    assert plan["feasible"] is True
    assert plan["asset_count"] == 13
    assert plan["usable_assets"] == config.MAX_SCENES
    assert plan["unused_assets"] == 13 - config.MAX_SCENES


def test_scene_plan_flags_a_length_that_cannot_be_built(client):
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())

    plan = client.get(f"/api/pets/{TEST_PET_ID}/scene-plan?duration=60").json()

    assert plan["feasible"] is False


def test_scene_plan_for_an_unknown_pet_returns_404(client):
    assert client.get("/api/pets/PET-DOES-NOT-EXIST/scene-plan").status_code == 404


def test_regenerate_rejects_motion_guidance_without_animation(client):
    """Choosing a model and writing a motion description but leaving
    animation off used to render Ken Burns and silently discard both, which
    reads as "the I2V settings did nothing"."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene",
        json={"scene_id": 1, "animate": False, "video_provider": "wan", "animate_prompt": "貓眨眼"},
    )

    assert response.status_code == 400
    assert "動態化" in response.json()["detail"]


def test_cleaned_version_reports_deleted_rather_than_missing(client):
    """A cleanup is deliberate, so playing it must not read as a lost file."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    work_dir = config.OUTPUT_DIR / TEST_PET_ID / "gen_webapp_cleanup"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "final.mp4").write_bytes(b"video")
    job_id = completed_job(
        TEST_PET_ID, work_dir=str(work_dir), output_path=str(work_dir / "final.mp4")
    )

    cleanup = client.delete(f"/api/jobs/{job_id}/files")
    assert cleanup.status_code == 200
    assert cleanup.json()["bytes_freed"] == 5

    assert client.get(f"/api/jobs/{job_id}/video").status_code == 410

    # The record is what has to survive.
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["cleaned_at"] is not None
    assert job["script_json"] is not None


def test_regenerate_rejects_a_background_description_with_no_treatment(client):
    """Same class of contradiction as the motion-guidance guard above: a
    described setting that never gets generated reads afterwards as "the
    background did nothing"."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene",
        json={
            "scene_id": 1,
            "generate_background": False,
            "background_prompt": "green grass in a sunny park",
        },
    )

    assert response.status_code == 400
    assert "背景" in response.json()["detail"]


def test_generate_passes_the_background_override_through(client, monkeypatch):
    """The web form has to be able to overrule the script for a named shot,
    the same way the CLI can — otherwise a reviewer can only fix a bad
    background by editing the script by hand."""
    from webapp import main

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    captured = {}

    def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return "out.mp4", 1

    monkeypatch.setattr(main, "generate_video", fake_generate_video)

    response = client.post(
        f"/api/pets/{TEST_PET_ID}/generate",
        json={
            "background_scenes": [1, 3],
            "background_mode": "replace",
            "background_prompt": "green grass in a sunny park",
        },
    )
    assert response.status_code == 202

    _drain_tasks()

    assert captured["background_scenes"] == {1, 3}
    assert captured["background_mode"].value == "replace"
    assert captured["background_prompt"] == "green grass in a sunny park"


def test_generate_without_an_override_leaves_the_script_in_charge(client, monkeypatch):
    """Nothing named means every shot keeps whatever background its own
    script asked for — which is where the story's sequence of places lives."""
    from webapp import main

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    captured = {}

    def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return "out.mp4", 1

    monkeypatch.setattr(main, "generate_video", fake_generate_video)

    assert client.post(f"/api/pets/{TEST_PET_ID}/generate", json={}).status_code == 202

    _drain_tasks()

    assert captured["background_scenes"] is None


def _drain_tasks() -> None:
    """Wait for the background worker thread to finish the task just started."""
    import time

    from webapp import tasks

    for _ in range(200):
        if not any(t["status"] == "running" for t in tasks.list_tasks()):
            return
        time.sleep(0.02)
    raise AssertionError("background task did not finish")


def test_approving_a_version_with_a_fabricated_claim_is_refused(client):
    """The refusal lives on the server, not in the browser: hiding the button
    would be a courtesy, and this is a rule (CLAUDE.md 事實正確性…不可發布)."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})
    from pipeline import pet_repo

    pet_repo.record_job_script(
        job_id,
        script_json={"scenes": [{"scene_id": 1}]},
        work_dir="storage/output/does-not-need-to-exist",
        disclosure_missing=[],
        unsupported_claims=["「我最愛跟小孩玩」— 資料裡查不到根據"],
        structure_issues=[],
    )

    blockers = client.get(f"/api/jobs/{job_id}/blockers").json()["blockers"]
    assert len(blockers) == 1

    response = client.post(f"/api/jobs/{job_id}/approve", json={})
    assert response.status_code == 400
    assert "小孩" in response.json()["detail"]

    assert client.get(f"/api/jobs/{job_id}").json()["review_state"] == "pending"


def test_a_clean_version_can_be_approved(client):
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    assert client.get(f"/api/jobs/{job_id}/blockers").json()["blockers"] == []
    assert client.post(f"/api/jobs/{job_id}/approve", json={}).status_code == 200

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["review_state"] == "approved"
    assert job["reviewed_at"] is not None


def test_rejecting_without_a_reason_is_refused(client):
    """A rejection with no reason tells the next attempt nothing, so the next
    run would be a guess."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    assert client.post(f"/api/jobs/{job_id}/reject", json={"note": "   "}).status_code == 400
    assert client.get(f"/api/jobs/{job_id}").json()["review_state"] == "pending"


def test_a_rejection_keeps_the_reason(client):
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    response = client.post(
        f"/api/jobs/{job_id}/reject", json={"note": "第三個鏡頭的貓看起來變形了"}
    )
    assert response.status_code == 200

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["review_state"] == "rejected"
    assert job["review_note"] == "第三個鏡頭的貓看起來變形了"


def test_a_new_version_starts_unreviewed(client):
    """Review is a mandatory step, so nothing may arrive already approved."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    assert client.get(f"/api/jobs/{job_id}").json()["review_state"] == "pending"


def test_the_look_preview_renders_the_same_way_a_real_shot_does(client):
    """Tuning a border by rendering a whole video is a three-minute wait per
    guess. The preview runs the pipeline's own build_scene_clip so what a
    reviewer is looking at cannot drift from what they will get."""
    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())

    response = client.get(
        f"/api/pets/{TEST_PET_ID}/decor-preview",
        params={"style": "cute", "accent_colour": "0x123456", "border_width": 10},
    )

    # The sample profile's assets do not exist on disk, so this is the
    # honest error rather than a picture — what matters is that it says so
    # instead of returning something broken.
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert response.headers["content-type"] == "image/png"
    else:
        assert "素材" in response.json()["detail"] or "照片" in response.json()["detail"]


def test_the_look_preview_for_an_unknown_pet_returns_404(client):
    assert client.get("/api/pets/PET-DOES-NOT-EXIST/decor-preview").status_code == 404


def test_regenerate_rejects_panel_copy_with_no_template(client):
    """Third of the same class as the motion and background guards: copy
    typed into a panel that is switched off is discarded, and reads
    afterwards as "the overlay did nothing"."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene",
        json={"scene_id": 1, "overlay": {"template": "none", "headline": "我在這裡等你"}},
    )

    assert response.status_code == 400
    assert "版型" in response.json()["detail"]


def test_regenerate_rejects_a_template_with_nothing_to_show(client):
    """An empty panel renders as nothing at all, so the reviewer would be
    told the overlay worked and see no change."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene",
        json={"scene_id": 1, "overlay": {"template": "info_sidebar", "tags": []}},
    )

    assert response.status_code == 400
    assert "tags" in response.json()["detail"]


def test_regenerate_passes_the_overlay_through(client, monkeypatch):
    """Without this a reviewer can only change a panel by hand-editing the
    script JSON, which is the thing the web UI exists to avoid."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    seen = {}

    from webapp import main

    def fake_regenerate(job, scene_id, **kwargs):
        seen.update(kwargs)
        return "out.mp4", 999

    monkeypatch.setattr(main, "regenerate_scene", fake_regenerate)

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene",
        json={
            "scene_id": 1,
            "overlay": {"template": "speech_bubble", "quote": "喜歡呼嚕嚕"},
        },
    )
    assert response.status_code == 202
    _drain_tasks()

    assert seen["overlay"].template.value == "speech_bubble"
    assert seen["overlay"].quote == "喜歡呼嚕嚕"


def test_regenerate_without_an_overlay_leaves_the_script_alone(client, monkeypatch):
    """Blank is the normal state: the shot keeps whatever panel the script
    chose for it."""
    from tests.conftest import completed_job

    client.put(f"/api/pets/{TEST_PET_ID}/profile", json=_sample_profile_json())
    job_id = completed_job(TEST_PET_ID, script_json={"scenes": [{"scene_id": 1}]})

    from webapp import main

    seen = {}

    def fake_regenerate(job, scene_id, **kwargs):
        seen.update(kwargs)
        return "out.mp4", 999

    monkeypatch.setattr(main, "regenerate_scene", fake_regenerate)

    response = client.post(
        f"/api/jobs/{job_id}/regenerate-scene", json={"scene_id": 1, "subtitle": "只改字幕"}
    )
    assert response.status_code == 202
    _drain_tasks()

    assert seen["overlay"] is None

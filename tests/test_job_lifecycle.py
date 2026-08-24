"""A run that dies must leave a FAILED job row, not silence.

These exercise the wiring between generate_video()/regenerate_scene() and
the job row only — the render itself is stubbed out, so they need neither
Ollama, FFmpeg nor a GPU.
"""

from __future__ import annotations

import pytest

from pipeline import db, pet_repo, regen, run
from pipeline.models import JobStatus
from tests.conftest import completed_job, sample_profile

TEST_PET_ID = "PET-TEST-LIFECYCLE"


def _db_reachable() -> bool:
    try:
        with db.engine.connect():
            pass
        return True
    except Exception:  # noqa: BLE001 - any failure here just means "skip"
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="PostgreSQL not reachable at DATABASE_URL"
)


def _script() -> dict:
    return {
        "pet_id": TEST_PET_ID,
        "style": "cute",
        "duration": 10,
        "scenes": [
            {
                "scene_id": 1,
                "start": 0,
                "end": 10,
                "visual_source": "IMG-001",
                "subtitle": "字幕",
                "narration": "旁白",
            }
        ],
    }


@pytest.fixture(autouse=True)
def _pet():
    pet_repo.save_pet(sample_profile(TEST_PET_ID, name="生命週期測試貓"))
    yield
    with db.get_session() as session:
        pet = session.get(pet_repo.Pet, TEST_PET_ID)
        if pet:
            session.delete(pet)


def test_generate_video_marks_the_job_failed_and_reraises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("ComfyUI server not reachable")

    monkeypatch.setattr(run, "_run_generation", boom)

    with pytest.raises(RuntimeError, match="ComfyUI"):
        run.generate_video(pet_id=TEST_PET_ID)

    jobs = pet_repo.list_generation_jobs(TEST_PET_ID)
    assert len(jobs) == 1
    assert jobs[0]["status"] == JobStatus.FAILED.value
    assert "ComfyUI server not reachable" in jobs[0]["error"]
    assert jobs[0]["output_path"] is None


def test_generate_video_on_an_unknown_pet_creates_no_job_row():
    """An unknown pet is a bad request, not a failed run — it must not leave
    a FAILED row behind."""
    before = len(pet_repo.list_generation_jobs(TEST_PET_ID))

    with pytest.raises(ValueError, match="No pet found"):
        run.generate_video(pet_id="PET-DOES-NOT-EXIST")

    assert len(pet_repo.list_generation_jobs(TEST_PET_ID)) == before


def test_regenerate_scene_marks_the_new_job_failed_and_links_its_parent(monkeypatch):
    parent_id = completed_job(TEST_PET_ID, script_json=_script())

    def boom(*args, **kwargs):
        raise RuntimeError("ffmpeg exited with 1")

    monkeypatch.setattr(regen, "_render_revision", boom)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        regen.regenerate_scene(parent_id, 1, subtitle="新字幕")

    failed = [j for j in pet_repo.list_generation_jobs(TEST_PET_ID) if j["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["parent_job_id"] == parent_id
    assert "ffmpeg exited with 1" in failed[0]["error"]

"""Reclaiming the disk a version's files hold, without losing the record.

The project requires every video to keep a record of which provider, prompt
and script produced it (CLAUDE.md 開發規範). Cleanup therefore deletes the
rendered files and keeps the row — the two are not the same thing.
"""

from __future__ import annotations

import pytest

from pipeline import config, db, pet_repo
from pipeline.models import JobStatus, Pet
from tests.conftest import sample_profile

TEST_PET_ID = "PET-TEST-CLEANUP"


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


@pytest.fixture(autouse=True)
def _pet():
    pet_repo.save_pet(sample_profile(TEST_PET_ID, name="清理測試貓"))
    yield
    with db.get_session() as session:
        pet = session.get(Pet, TEST_PET_ID)
        if pet:
            session.delete(pet)


def _job_with_files(script: dict | None = None) -> tuple[int, object]:
    """A finished job whose work_dir holds a rendered video."""
    return _job_with_files_at("gen_cleanup_test", script)


def _job_with_files_at(dir_name: str, script: dict | None = None) -> tuple[int, object]:
    work_dir = config.OUTPUT_DIR / TEST_PET_ID / dir_name
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "scene_1.mp4").write_bytes(b"x" * 2048)
    output = work_dir / "final.mp4"
    output.write_bytes(b"y" * 1024)

    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=30)
    pet_repo.record_job_script(
        job_id,
        script_json=script or {"scenes": [{"scene_id": 1, "visual_source": "IMG-001"}]},
        work_dir=str(work_dir),
        disclosure_missing=[],
        structure_issues=[],
    )
    pet_repo.finish_generation_job(job_id, output_path=str(output))
    return job_id, work_dir


def test_cleanup_deletes_the_files_and_reports_what_it_freed():
    job_id, work_dir = _job_with_files()

    result = pet_repo.cleanup_generation_job(job_id)

    assert not work_dir.exists()
    assert result["bytes_freed"] == 3072
    assert result["already_clean"] is False


def test_cleanup_keeps_the_generation_record():
    """The provenance is the part that has to survive."""
    script = {"scenes": [{"scene_id": 1, "visual_source": "IMG-001"}]}
    job_id, _ = _job_with_files(script)

    pet_repo.cleanup_generation_job(job_id)
    job = pet_repo.get_generation_job(job_id)

    assert job is not None
    assert job["script_json"] == script
    assert job["status"] == JobStatus.DONE.value
    assert job["cleaned_at"] is not None


def test_cleaning_twice_is_not_an_error():
    """The point is for the files to be gone, which they already are."""
    job_id, _ = _job_with_files()
    pet_repo.cleanup_generation_job(job_id)

    result = pet_repo.cleanup_generation_job(job_id)

    assert result["already_clean"] is True
    assert result["bytes_freed"] == 0


def test_cleanup_refuses_a_work_dir_outside_the_output_directory(tmp_path):
    """work_dir is a stored string; a delete driven by stored state must not
    be able to reach the rest of the disk if that string is ever wrong."""
    stray = tmp_path / "not_output"
    stray.mkdir()
    (stray / "important.txt").write_text("keep me")

    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=30)
    pet_repo.record_job_script(
        job_id,
        script_json={},
        work_dir=str(stray),
        disclosure_missing=[],
        structure_issues=[],
    )
    pet_repo.finish_generation_job(job_id, output_path=str(stray / "final.mp4"))

    with pytest.raises(ValueError, match="Refusing to delete"):
        pet_repo.cleanup_generation_job(job_id)

    assert (stray / "important.txt").exists()


def test_cleanup_refuses_a_running_job():
    """Its files are still being written."""
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=30)

    with pytest.raises(ValueError, match="still running"):
        pet_repo.cleanup_generation_job(job_id)


def test_batch_cleanup_keeps_the_newest_and_clears_the_rest():
    """A debugging session leaves a pile; clearing it one version at a time
    is its own chore."""
    job_ids = [_job_with_files_at(f"gen_batch_{i}")[0] for i in range(4)]

    result = pet_repo.cleanup_old_generation_jobs(TEST_PET_ID, keep=2)

    # list_generation_jobs is newest-first, so the two newest survive.
    assert set(result["cleaned"]) == set(job_ids[:2])
    remaining = {job["id"]: job["cleaned_at"] for job in pet_repo.list_generation_jobs(TEST_PET_ID)}
    assert remaining[job_ids[-1]] is None
    assert remaining[job_ids[0]] is not None


def test_batch_cleanup_leaves_a_running_job_alone():
    """Its files are still being written."""
    running = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=30)
    _job_with_files_at("gen_batch_running")

    result = pet_repo.cleanup_old_generation_jobs(TEST_PET_ID, keep=0)

    assert running not in result["cleaned"]

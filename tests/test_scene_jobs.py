"""Per-scene job tracking and resume.

The behaviour under test is the expensive one: a run that dies on a later
scene must not re-render the scenes an earlier attempt already finished.
FFmpeg and the I2V providers are stubbed out — what matters here is which
scenes get rendered, not what the clips look like.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import db, pet_repo, rendering, run
from pipeline.models import JobStatus
from pipeline.scene_tracking import DatabaseSceneTracker, NoopSceneTracker
from tests.conftest import sample_profile

TEST_PET_ID = "PET-TEST-SCENEJOBS"


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


class StubVideoProvider:
    """Stands in for an I2V provider. Carries the name it was asked for so
    tests can assert which one rendering picked, and a preflight that always
    passes (render_script checks provider availability before narration)."""

    def __init__(self, name: str):
        self.name = name

    def preflight(self) -> None: ...


def _script(scene_count: int = 3) -> dict:
    return {
        "pet_id": TEST_PET_ID,
        "style": "cute",
        "duration": scene_count * 5,
        "scenes": [
            {
                "scene_id": i,
                "start": (i - 1) * 5,
                "end": i * 5,
                "visual_source": f"IMG-00{i}",
                "subtitle": f"字幕{i}",
                "narration": f"旁白{i}",
            }
            for i in range(1, scene_count + 1)
        ],
    }


@pytest.fixture(autouse=True)
def _pet():
    pet_repo.save_pet(sample_profile(TEST_PET_ID, name="鏡頭測試貓"))
    yield
    with db.get_session() as session:
        pet = session.get(pet_repo.Pet, TEST_PET_ID)
        if pet:
            session.delete(pet)


@pytest.fixture
def stub_render(monkeypatch, tmp_path):
    """Replace the media work with file-touching stubs and report which
    scenes were actually rendered."""
    rendered: list[int] = []

    def fake_build_scene_clip(
        *, visual_path, duration, subtitle_text, output_path, disclosure_text=None
    ):
        rendered.append(int(Path(output_path).stem.split("_")[1]))
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", fake_build_scene_clip)
    monkeypatch.setattr(
        rendering, "_resolve_visual_path", lambda profile, source: tmp_path / "photo.jpg"
    )
    monkeypatch.setattr(
        rendering,
        "silence_scenes",
        lambda script, out: {s["scene_id"]: "a.wav" for s in script["scenes"]},
    )
    monkeypatch.setattr(rendering, "get_video_provider", StubVideoProvider)
    monkeypatch.setattr(
        rendering,
        "animate_photo",
        lambda src, provider, *, duration, output_path, prompt: Path(output_path).write_bytes(
            b"i2v"
        ),
    )
    monkeypatch.setattr(rendering, "concat_video_only", lambda clips, out: out)
    monkeypatch.setattr(rendering, "concat_audio", lambda paths, out: out)
    monkeypatch.setattr(rendering, "mux_video_audio", lambda v, a, out: out)
    (tmp_path / "photo.jpg").write_bytes(b"jpg")
    return rendered


def test_render_records_one_row_per_scene_with_its_provenance(stub_render, tmp_path):
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)

    rendering.render_script(
        sample_profile(TEST_PET_ID),
        _script(),
        tmp_path / "gen_1",
        animate_scenes={2},
        video_provider="wan",
        animate_prompt="貓輕輕搖尾巴",
        scene_tracker=DatabaseSceneTracker(job_id),
    )

    scenes = pet_repo.list_scene_jobs(job_id)
    assert [s["scene_id"] for s in scenes] == [1, 2, 3]
    assert all(s["status"] == JobStatus.DONE.value for s in scenes)
    assert [s["visual_source"] for s in scenes] == ["IMG-001", "IMG-002", "IMG-003"]
    # Provenance is recorded only against the scene that actually went
    # through the I2V provider; the other two were plain edits, and claiming
    # "wan" for them would misrepresent how they were made.
    assert [s["video_provider"] for s in scenes] == [None, "wan", None]
    assert [s["animate_prompt"] for s in scenes] == [None, "貓輕輕搖尾巴", None]


def test_a_scene_that_fails_is_recorded_before_the_error_propagates(
    stub_render, tmp_path, monkeypatch
):
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)

    def explode_on_scene_2(
        *, visual_path, duration, subtitle_text, output_path, disclosure_text=None
    ):
        scene_id = int(Path(output_path).stem.split("_")[1])
        if scene_id == 2:
            raise RuntimeError("ffmpeg exited with 1")
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", explode_on_scene_2)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        rendering.render_script(
            sample_profile(TEST_PET_ID),
            _script(),
            tmp_path / "gen_1",
            scene_tracker=DatabaseSceneTracker(job_id),
        )

    by_id = {s["scene_id"]: s for s in pet_repo.list_scene_jobs(job_id)}
    assert by_id[1]["status"] == JobStatus.DONE.value
    assert by_id[2]["status"] == JobStatus.FAILED.value
    assert "ffmpeg exited with 1" in by_id[2]["error"]
    # Scene 3 was never reached, so it has no row at all.
    assert 3 not in by_id


def test_resume_reuses_finished_clips_and_only_renders_what_is_left(
    stub_render, tmp_path, monkeypatch
):
    """The whole reason per-scene jobs exist."""
    work_dir = tmp_path / "gen_1"
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)
    pet_repo.record_job_script(
        job_id,
        script_json=_script(),
        work_dir=str(work_dir),
        disclosure_missing=[],
        structure_issues=[],
    )

    def explode_on_scene_3(
        *, visual_path, duration, subtitle_text, output_path, disclosure_text=None
    ):
        scene_id = int(Path(output_path).stem.split("_")[1])
        if scene_id == 3:
            raise RuntimeError("ComfyUI server not reachable")
        stub_render.append(scene_id)
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", explode_on_scene_3)
    with pytest.raises(RuntimeError):
        rendering.render_script(
            sample_profile(TEST_PET_ID),
            _script(),
            work_dir,
            scene_tracker=DatabaseSceneTracker(job_id),
        )
    assert stub_render == [1, 2]

    # Second attempt, with whatever broke scene 3 now fixed: scenes 1 and 2
    # are on disk and marked done.
    def working_build(*, visual_path, duration, subtitle_text, output_path, disclosure_text=None):
        stub_render.append(int(Path(output_path).stem.split("_")[1]))
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", working_build)
    stub_render.clear()
    run.resume_generation_job(job_id)

    assert stub_render == [3], "resume must skip the scenes the failed attempt finished"
    assert pet_repo.get_generation_job(job_id)["status"] == JobStatus.DONE.value


def test_resume_refuses_a_job_that_never_recorded_a_script():
    """Nothing to continue from — a fresh run is the only honest answer."""
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)
    pet_repo.fail_generation_job(job_id, "died during script generation")

    with pytest.raises(ValueError, match="before its script was recorded"):
        run.resume_generation_job(job_id)


def test_resume_refuses_a_finished_job(stub_render, tmp_path):
    from tests.conftest import completed_job

    job_id = completed_job(TEST_PET_ID, script_json=_script())

    with pytest.raises(ValueError, match="already finished"):
        run.resume_generation_job(job_id)


def test_noop_tracker_never_reuses_anything():
    """render_script()'s default must render every scene — a caller with no
    job row has nothing to resume from."""
    assert NoopSceneTracker().reusable_clip(1) is None


def test_resume_reuses_the_original_animation_settings(stub_render, tmp_path, monkeypatch):
    """Resume must finish the video that was interrupted. If the animation
    settings weren't stored on the job, the scenes it hadn't reached yet
    would quietly come out as Ken Burns instead of Image-to-Video."""
    work_dir = tmp_path / "gen_1"
    job_id = pet_repo.start_generation_job(
        TEST_PET_ID,
        style="cute",
        duration=15,
        animate_scenes={3},
        video_provider="wan",
        animate_prompt="貓輕輕搖尾巴",
    )
    pet_repo.record_job_script(
        job_id,
        script_json=_script(),
        work_dir=str(work_dir),
        disclosure_missing=[],
        structure_issues=[],
    )

    # Scene 3 is the animated one and the one that died.
    def explode_on_scene_3(
        *, visual_path, duration, subtitle_text, output_path, disclosure_text=None
    ):
        scene_id = int(Path(output_path).stem.split("_")[1])
        if scene_id == 3:
            raise RuntimeError("ComfyUI server not reachable")
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", explode_on_scene_3)
    with pytest.raises(RuntimeError):
        rendering.render_script(
            sample_profile(TEST_PET_ID),
            _script(),
            work_dir,
            animate_scenes={3},
            video_provider="wan",
            animate_prompt="貓輕輕搖尾巴",
            scene_tracker=DatabaseSceneTracker(job_id),
        )

    animated: list[tuple[str, str | None]] = []

    def record_animation(src, provider, *, duration, output_path, prompt):
        animated.append((provider.name, prompt))
        Path(output_path).write_bytes(b"i2v")

    monkeypatch.setattr(rendering, "animate_photo", record_animation)

    def working_build(*, visual_path, duration, subtitle_text, output_path, disclosure_text=None):
        Path(output_path).write_bytes(b"clip")
        return output_path

    monkeypatch.setattr(rendering, "build_scene_clip", working_build)
    run.resume_generation_job(job_id)

    assert animated == [("wan", "貓輕輕搖尾巴")], (
        "the resumed scene must still go through the original I2V provider and prompt"
    )


def test_reaping_closes_running_jobs_and_their_unfinished_scenes():
    """A run killed mid-flight leaves a row claiming to still be running.
    Startup closes it so the UI shows 中斷 instead of 生成中 forever."""
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)
    pet_repo.start_scene_job(job_id, 1)
    pet_repo.finish_scene_job(job_id, 1, clip_path="scene_1.mp4")
    pet_repo.start_scene_job(job_id, 2)  # still "running" when the process died

    reaped = pet_repo.reap_interrupted_jobs("伺服器重新啟動")

    assert job_id in reaped
    job = pet_repo.get_generation_job(job_id)
    assert job["status"] == JobStatus.FAILED.value
    assert job["error"] == "伺服器重新啟動"

    by_id = {s["scene_id"]: s for s in pet_repo.list_scene_jobs(job_id)}
    # The finished scene keeps its clip — that is what a resume reuses.
    assert by_id[1]["status"] == JobStatus.DONE.value
    assert by_id[1]["clip_path"] == "scene_1.mp4"
    assert by_id[2]["status"] == JobStatus.FAILED.value


def test_reaping_leaves_finished_jobs_alone(stub_render):
    from tests.conftest import completed_job

    job_id = completed_job(TEST_PET_ID, script_json=_script())

    pet_repo.reap_interrupted_jobs("伺服器重新啟動")

    assert pet_repo.get_generation_job(job_id)["status"] == JobStatus.DONE.value


def test_a_cli_run_reaped_by_a_restart_heals_when_it_finishes():
    """The one case reaping gets wrong: a CLI run in flight while the web app
    boots. It is self-correcting because the CLI still owns the row."""
    job_id = pet_repo.start_generation_job(TEST_PET_ID, style="cute", duration=15)
    pet_repo.reap_interrupted_jobs("伺服器重新啟動")
    assert pet_repo.get_generation_job(job_id)["status"] == JobStatus.FAILED.value

    pet_repo.finish_generation_job(job_id, output_path="out.mp4")

    job = pet_repo.get_generation_job(job_id)
    assert job["status"] == JobStatus.DONE.value
    assert job["error"] is None

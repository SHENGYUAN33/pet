from __future__ import annotations

from datetime import UTC, datetime

from pipeline.db import get_session
from pipeline.models import GenerationJob, JobStatus, Pet, SceneJob
from pipeline.profile import PetProfile


def save_pet(profile: PetProfile) -> None:
    """Insert or overwrite the pet's catalog entry."""
    profile_json = profile.model_dump(mode="json")
    with get_session() as session:
        existing = session.get(Pet, profile.pet_id)
        if existing:
            existing.name = profile.name
            existing.species = profile.species
            existing.profile_json = profile_json
        else:
            session.add(
                Pet(
                    pet_id=profile.pet_id,
                    name=profile.name,
                    species=profile.species,
                    profile_json=profile_json,
                )
            )


def get_pet(pet_id: str) -> PetProfile | None:
    with get_session() as session:
        row = session.get(Pet, pet_id)
        return PetProfile.model_validate(row.profile_json) if row else None


def list_pets() -> list[PetProfile]:
    with get_session() as session:
        rows = session.query(Pet).order_by(Pet.pet_id).all()
        return [PetProfile.model_validate(row.profile_json) for row in rows]


def _require_job(session, job_id: int) -> GenerationJob:
    job = session.get(GenerationJob, job_id)
    if job is None:
        raise ValueError(f"No generation job found with id {job_id}")
    return job


def start_generation_job(
    pet_id: str,
    *,
    style: str,
    duration: int,
    parent_job_id: int | None = None,
    voice_sample: str | None = None,
    music_track: str | None = None,
    animate_scenes: set[int] | None = None,
    video_provider: str | None = None,
    animate_prompt: str | None = None,
) -> int:
    """Open a RUNNING job row before the work begins and return its id.

    Called first so that a run which crashes, is cancelled, or is cut off by
    a server restart still leaves a record of having been attempted — see
    pipeline/models.py GenerationJob. Finish it with finish_generation_job()
    or fail_generation_job()."""
    with get_session() as session:
        job = GenerationJob(
            pet_id=pet_id,
            style=style,
            duration=duration,
            status=JobStatus.RUNNING.value,
            voice_sample=voice_sample,
            music_track=music_track,
            animate_scenes=sorted(animate_scenes) if animate_scenes else None,
            video_provider=video_provider,
            animate_prompt=animate_prompt,
            disclosure_missing={"missing_restrictions": []},
            structure_issues={"issues": []},
            script_json={},
        )
        if parent_job_id is not None:
            job.parent_job_id = parent_job_id
        session.add(job)
        session.flush()  # assigns job.id without waiting for the outer commit
        return job.id


def record_job_script(
    job_id: int,
    *,
    script_json: dict,
    work_dir: str,
    disclosure_missing: list[str],
    structure_issues: list[str],
) -> None:
    """Attach the chosen script and its render directory partway through the
    run, before the slow scene rendering starts.

    Written here rather than at the end because resuming needs both: the
    script says what to render, work_dir says where the clips that already
    exist are."""
    with get_session() as session:
        job = _require_job(session, job_id)
        job.script_json = script_json
        job.work_dir = work_dir
        job.disclosure_missing = {"missing_restrictions": disclosure_missing}
        job.structure_issues = {"issues": structure_issues}


def finish_generation_job(job_id: int, *, output_path: str) -> None:
    """Mark a job DONE. The script and check results were already attached by
    record_job_script(); all that is left is the file it produced."""
    with get_session() as session:
        job = _require_job(session, job_id)
        job.status = JobStatus.DONE.value
        job.output_path = output_path
        job.error = None
        job.finished_at = datetime.now(UTC)


def fail_generation_job(job_id: int, error: str) -> None:
    """Mark a job FAILED with the reason, so the reviewer sees why instead of
    a run that silently vanished."""
    with get_session() as session:
        job = _require_job(session, job_id)
        job.status = JobStatus.FAILED.value
        job.error = error
        job.finished_at = datetime.now(UTC)


def get_generation_job(job_id: int) -> dict | None:
    """Full job record including script_json, for regenerate_scene() to
    load "what this job used" from. list_generation_jobs() intentionally
    omits script_json to keep bulk listings short."""
    with get_session() as session:
        row = session.get(GenerationJob, job_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "pet_id": row.pet_id,
            "style": row.style,
            "duration": row.duration,
            "status": row.status,
            "output_path": row.output_path,
            "error": row.error,
            "work_dir": row.work_dir,
            "voice_sample": row.voice_sample,
            "music_track": row.music_track,
            "animate_scenes": row.animate_scenes,
            "video_provider": row.video_provider,
            "animate_prompt": row.animate_prompt,
            "script_json": row.script_json,
            "parent_job_id": row.parent_job_id,
        }


def list_generation_jobs(pet_id: str) -> list[dict]:
    with get_session() as session:
        rows = (
            session.query(GenerationJob)
            .filter(GenerationJob.pet_id == pet_id)
            .order_by(GenerationJob.created_at.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "style": row.style,
                "duration": row.duration,
                "status": row.status,
                "output_path": row.output_path,
                "error": row.error,
                "disclosure_missing": row.disclosure_missing,
                "structure_issues": row.structure_issues,
                "scene_count": len(row.script_json.get("scenes", [])),
                "parent_job_id": row.parent_job_id,
                "created_at": row.created_at.isoformat(),
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
            for row in rows
        ]


def start_scene_job(
    job_id: int,
    scene_id: int,
    *,
    visual_source: str | None = None,
    video_provider: str | None = None,
    animate_prompt: str | None = None,
) -> None:
    """Open (or reopen, on a retry) the row for one scene of a run.

    Upsert rather than insert: a resumed run walks the same scenes again,
    and a second row for the same scene would make "is this scene done?"
    ambiguous. A reopened row keeps its history by bumping attempt."""
    with get_session() as session:
        scene = _get_scene_job(session, job_id, scene_id)
        if scene is None:
            session.add(
                SceneJob(
                    job_id=job_id,
                    scene_id=scene_id,
                    status=JobStatus.RUNNING.value,
                    attempt=1,
                    visual_source=visual_source,
                    video_provider=video_provider,
                    animate_prompt=animate_prompt,
                )
            )
            return
        scene.status = JobStatus.RUNNING.value
        scene.attempt += 1
        scene.visual_source = visual_source
        scene.video_provider = video_provider
        scene.animate_prompt = animate_prompt
        scene.clip_path = None
        scene.error = None
        scene.finished_at = None


def finish_scene_job(job_id: int, scene_id: int, *, clip_path: str) -> None:
    with get_session() as session:
        scene = _require_scene_job(session, job_id, scene_id)
        scene.status = JobStatus.DONE.value
        scene.clip_path = clip_path
        scene.error = None
        scene.finished_at = datetime.now(UTC)


def fail_scene_job(job_id: int, scene_id: int, error: str) -> None:
    with get_session() as session:
        scene = _require_scene_job(session, job_id, scene_id)
        scene.status = JobStatus.FAILED.value
        scene.error = error
        scene.finished_at = datetime.now(UTC)


def get_finished_scene_clip(job_id: int, scene_id: int) -> str | None:
    """The clip path of a scene already rendered for this job, or None.

    Only the database's opinion — the caller still has to check the file is
    there, since work_dir can be cleaned up independently of these rows."""
    with get_session() as session:
        scene = _get_scene_job(session, job_id, scene_id)
        if scene is None or scene.status != JobStatus.DONE.value:
            return None
        return scene.clip_path


def list_scene_jobs(job_id: int) -> list[dict]:
    """Per-scene provenance and status for one run, in scene order."""
    with get_session() as session:
        rows = (
            session.query(SceneJob)
            .filter(SceneJob.job_id == job_id)
            .order_by(SceneJob.scene_id)
            .all()
        )
        return [
            {
                "scene_id": row.scene_id,
                "status": row.status,
                "attempt": row.attempt,
                "visual_source": row.visual_source,
                "video_provider": row.video_provider,
                "animate_prompt": row.animate_prompt,
                "clip_path": row.clip_path,
                "error": row.error,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
            for row in rows
        ]


def _get_scene_job(session, job_id: int, scene_id: int) -> SceneJob | None:
    return (
        session.query(SceneJob)
        .filter(SceneJob.job_id == job_id, SceneJob.scene_id == scene_id)
        .one_or_none()
    )


def _require_scene_job(session, job_id: int, scene_id: int) -> SceneJob:
    scene = _get_scene_job(session, job_id, scene_id)
    if scene is None:
        raise ValueError(f"No scene job for job {job_id} scene {scene_id}")
    return scene


def reap_interrupted_jobs(reason: str) -> list[int]:
    """Close jobs left RUNNING with no process behind them and return their ids.

    A job row is opened when a run starts and closed when it ends, so a run
    killed mid-flight (server restart, Ctrl-C, power loss) leaves a row that
    claims to still be running forever. Called at web app startup, when any
    row still marked RUNNING must be a leftover: the threads that owned them
    died with the previous process.

    Their scenes are closed the same way, except the ones that had already
    finished — those clips are on disk and are exactly what a resume reuses.

    A run started from the CLI while the web app boots would be reaped here
    too. That is wrong but self-correcting: the CLI still owns the row and
    finish_generation_job() clears the status and error when it completes.
    """
    with get_session() as session:
        jobs = (
            session.query(GenerationJob)
            .filter(GenerationJob.status == JobStatus.RUNNING.value)
            .all()
        )
        now = datetime.now(UTC)
        for job in jobs:
            job.status = JobStatus.FAILED.value
            job.error = reason
            job.finished_at = now
        job_ids = [job.id for job in jobs]

        if job_ids:
            (
                session.query(SceneJob)
                .filter(
                    SceneJob.job_id.in_(job_ids),
                    SceneJob.status == JobStatus.RUNNING.value,
                )
                .update(
                    {"status": JobStatus.FAILED.value, "error": reason, "finished_at": now},
                    synchronize_session=False,
                )
            )
        return job_ids

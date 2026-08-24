from __future__ import annotations

from datetime import UTC, datetime

from pipeline.db import get_session
from pipeline.models import GenerationJob, JobStatus, Pet
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


def start_generation_job(
    pet_id: str,
    *,
    style: str,
    duration: int,
    parent_job_id: int | None = None,
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
            disclosure_missing={"missing_restrictions": []},
            structure_issues={"issues": []},
            script_json={},
        )
        if parent_job_id is not None:
            job.parent_job_id = parent_job_id
        session.add(job)
        session.flush()  # assigns job.id without waiting for the outer commit
        return job.id


def finish_generation_job(
    job_id: int,
    *,
    output_path: str,
    disclosure_missing: list[str],
    structure_issues: list[str],
    script_json: dict,
) -> None:
    """Mark a job DONE and attach what the finished run produced."""
    with get_session() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise ValueError(f"No generation job found with id {job_id}")
        job.status = JobStatus.DONE.value
        job.output_path = output_path
        job.disclosure_missing = {"missing_restrictions": disclosure_missing}
        job.structure_issues = {"issues": structure_issues}
        job.script_json = script_json
        job.finished_at = datetime.now(UTC)


def fail_generation_job(job_id: int, error: str) -> None:
    """Mark a job FAILED with the reason, so the reviewer sees why instead of
    a run that silently vanished."""
    with get_session() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise ValueError(f"No generation job found with id {job_id}")
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

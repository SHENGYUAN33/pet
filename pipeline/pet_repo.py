from __future__ import annotations

from pipeline.db import get_session
from pipeline.models import GenerationJob, Pet
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


def record_generation_job(
    pet_id: str,
    *,
    style: str,
    duration: int,
    output_path: str,
    disclosure_missing: list[str],
    structure_issues: list[str],
    script_json: dict,
    parent_job_id: int | None = None,
) -> int:
    """Record one completed generate_video()/regenerate_scene() run (see
    pipeline/models.py GenerationJob docstring) and return its id."""
    with get_session() as session:
        job = GenerationJob(
            pet_id=pet_id,
            style=style,
            duration=duration,
            output_path=output_path,
            disclosure_missing={"missing_restrictions": disclosure_missing},
            structure_issues={"issues": structure_issues},
            script_json=script_json,
            parent_job_id=parent_job_id,
        )
        session.add(job)
        session.flush()  # assigns job.id without waiting for the outer commit
        return job.id


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
            "output_path": row.output_path,
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
                "output_path": row.output_path,
                "disclosure_missing": row.disclosure_missing,
                "structure_issues": row.structure_issues,
                "scene_count": len(row.script_json.get("scenes", [])),
                "parent_job_id": row.parent_job_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

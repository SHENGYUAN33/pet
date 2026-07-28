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
) -> None:
    """Flat completed-run log (see pipeline/models.py GenerationJob docstring
    — not the full async per-scene state machine yet)."""
    with get_session() as session:
        session.add(
            GenerationJob(
                pet_id=pet_id,
                style=style,
                duration=duration,
                output_path=output_path,
                disclosure_missing={"missing_restrictions": disclosure_missing},
                structure_issues={"issues": structure_issues},
            )
        )


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
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

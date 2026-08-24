"""Shared test helpers."""

from __future__ import annotations

from pipeline import pet_repo
from pipeline.profile import PetProfile


def completed_job(
    pet_id: str,
    *,
    style: str = "cute",
    duration: int = 30,
    output_path: str = "storage/output/does-not-need-to-exist.mp4",
    disclosure_missing: list[str] | None = None,
    structure_issues: list[str] | None = None,
    script_json: dict | None = None,
    parent_job_id: int | None = None,
) -> int:
    """Write a job row that is already DONE and return its id.

    Tests that need a finished job to read back shouldn't have to spell out
    the start/finish pair every time — the pipeline splits those two so a
    crash mid-run leaves a record, which is irrelevant to a fixture.
    """
    job_id = pet_repo.start_generation_job(
        pet_id, style=style, duration=duration, parent_job_id=parent_job_id
    )
    pet_repo.finish_generation_job(
        job_id,
        output_path=output_path,
        disclosure_missing=disclosure_missing or [],
        structure_issues=structure_issues or [],
        script_json=script_json or {},
    )
    return job_id


def sample_profile(pet_id: str, *, name: str = "測試貓") -> PetProfile:
    """A minimal PetProfile that passes validation — for tests about
    something other than the profile schema itself."""
    return PetProfile.model_validate(
        {
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
    )

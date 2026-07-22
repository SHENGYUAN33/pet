from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class HealthStatus(BaseModel):
    vaccinated: bool
    neutered: bool
    microchipped: bool


class PersonalityTags(BaseModel):
    appeal: list[str] = []
    lifestyle_fit: list[str] = []
    care_needs: list[str] = []
    restrictions: list[str] = []


class MediaAsset(BaseModel):
    asset_id: str
    type: str
    url: str
    captured_at: str | None = None
    usage_license_status: str = "granted"
    ai_extension_allowed: bool = True


class Media(BaseModel):
    assets: list[MediaAsset] = []


class IdentityCard(BaseModel):
    coat_color: str | None = None
    pattern: str | None = None
    ear_type: str | None = None
    eye_color: str | None = None
    body_type: str | None = None
    accessories: str | None = None
    distinguishing_marks: str | None = None


class PetProfile(BaseModel):
    """Mirrors docs/schemas/pet_profile.example.json — the single source of
    truth used by script generation and (in later phases) fact-checking."""

    pet_id: str
    name: str
    species: str
    breed: str | None = None
    sex: str
    age: str
    size: str
    location: str | None = None
    health_status: HealthStatus
    personality_tags: PersonalityTags
    story: str | None = None
    adoption_requirements: list[str] = []
    contact_url: str
    media: Media
    identity_card: IdentityCard

    @classmethod
    def load(cls, path: str | Path) -> "PetProfile":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

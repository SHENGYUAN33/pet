from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Pet(Base):
    """Canonical pet catalog entry. profile_json holds the full validated
    PetProfile (pipeline/profile.py) payload — media/identity_card/etc. stay
    nested rather than normalized into columns, matching how the rest of the
    pipeline already treats Profile as one structured document."""

    __tablename__ = "pets"

    pet_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    species: Mapped[str] = mapped_column(String)
    profile_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    generation_jobs: Mapped[list[GenerationJob]] = relationship(
        back_populates="pet", cascade="all, delete-orphan"
    )


class GenerationJob(Base):
    """One row per completed pipeline.run.generate_video() call. This is a
    flat completed-run log for MVP slice 1 (多寵物管理＋資料層) — not the full
    async per-scene Job state machine from docs/architecture.md §10, which is
    the next slice (分鏡編輯＋單鏡頭重生)."""

    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.pet_id"))
    style: Mapped[str] = mapped_column(String)
    duration: Mapped[int] = mapped_column(Integer)
    output_path: Mapped[str] = mapped_column(String)
    disclosure_missing: Mapped[dict] = mapped_column(JSONB)
    structure_issues: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pet: Mapped[Pet] = relationship(back_populates="generation_jobs")

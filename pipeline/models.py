from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    """Lifecycle of one generation run.

    A deliberate subset of the state machine in docs/architecture.md §10:
    only the states something actually produces today. The script-approval,
    publish and performance-tracking states there wait for the features they
    describe, and a PENDING state waits for a real queue — right now a job
    starts running the moment it is created.

    str-valued so the column stays readable in psql and JSON responses need
    no conversion.
    """

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


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
    """One row per generation or single-shot regeneration
    (pipeline.run.generate_video / pipeline.regen.regenerate_scene).

    The row is written when the run *starts*, not when it finishes: a run
    with Image-to-Video takes minutes per scene, and a crash or a server
    restart used to leave no trace of it at all. created_at is therefore
    the start time and finished_at is set on the way out; status says which
    of the two the row is currently between.

    Fields only known once the run gets that far start empty rather than
    NULL (script_json {}, the two check results with empty lists), so
    readers never have to distinguish "no script yet" from "no value".
    output_path is genuinely absent until the render succeeds, so it is
    nullable, as is error, which carries the failure reason for FAILED.

    script_json is the exact script (scenes + disclosure/structure check
    results) actually rendered, so a later regenerate_scene() call can load
    "what this job used" without re-deriving it. parent_job_id links a
    revision back to the job it was regenerated from (None for a fresh
    generate_video() run) — revisions are new rows, never overwrites, so
    prior output files and their audit trail stay intact."""

    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.pet_id"))
    style: Mapped[str] = mapped_column(String)
    duration: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default=JobStatus.RUNNING.value)
    output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    disclosure_missing: Mapped[dict] = mapped_column(JSONB)
    structure_issues: Mapped[dict] = mapped_column(JSONB)
    script_json: Mapped[dict] = mapped_column(JSONB)
    # ondelete="SET NULL": deleting a pet cascades to delete all its jobs in
    # one statement with no guaranteed order, so a parent job could be
    # deleted before its revision — without SET NULL that trips this
    # self-referential FK's integrity check (found by test_pet_repo.py).
    parent_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pet: Mapped[Pet] = relationship(back_populates="generation_jobs")

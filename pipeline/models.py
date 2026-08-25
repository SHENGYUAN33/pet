from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
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
    # Where this run's scene clips live. Kept so a resumed run writes into
    # the same directory and can reuse the clips already rendered there.
    work_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    # Everything a resumed run needs to reproduce the video that was being
    # made. Without these it would silently make a *different* one: no
    # narration or music (the two paths below), and Ken Burns instead of
    # Image-to-Video for the scenes it had not reached yet. Paths to local
    # files, not secrets.
    voice_sample: Mapped[str | None] = mapped_column(String, nullable=True)
    music_track: Mapped[str | None] = mapped_column(String, nullable=True)
    animate_scenes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    video_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    animate_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    # The same reasoning for the generated-background pass
    # (pipeline/outpaint.py): a resumed run without these would finish the
    # video with blurred bars on the scenes it had not reached yet, next to
    # generated surroundings on the ones it had.
    outpaint_scenes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    image_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    outpaint_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
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
    # Set when this run's rendered files are deleted to reclaim space. The row
    # itself stays: the project requires every video to keep a record of which
    # provider, prompt and script produced it (CLAUDE.md 開發規範), so a
    # cleanup removes the output, never the provenance. output_path is left
    # in place as part of that record and must not be played once this is set.
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pet: Mapped[Pet] = relationship(back_populates="generation_jobs")
    scene_jobs: Mapped[list[SceneJob]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class SceneJob(Base):
    """One row per scene of one generation run (docs/architecture.md §10's
    per-scene jobs).

    Two things depend on this. Resuming: a Wan2.2 scene costs ~8 minutes, so
    a run that dies on scene 5 must not throw away scenes 1-4 — their clips
    are already on disk under the job's work_dir, and a DONE row here is
    what says a clip is finished rather than half-written. Provenance: the
    project requires every video to record which provider and prompt
    produced each shot (CLAUDE.md 開發規範), which until now lived only in
    function arguments and was lost the moment the run ended.

    One row per (job_id, scene_id); a retry updates the row and bumps
    attempt rather than inserting a second one. video_provider and
    animate_prompt are NULL for the scenes that were not animated — most of
    them, since real footage and Ken Burns are the default (strategy A) —
    and image_provider/outpaint_prompt likewise for the scenes whose
    background was not generated. A shot carrying any generated content has
    to stay identifiable afterwards: it answers "was this real?" and it is
    what the AI-generation disclosure has to be driven from.

    Seed is not recorded yet: no VideoGenerationProvider reports the seed it
    used back to the caller, so there is nothing truthful to store. It
    belongs here once the provider interface returns it.
    """

    __tablename__ = "scene_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default=JobStatus.RUNNING.value)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    visual_source: Mapped[str | None] = mapped_column(String, nullable=True)
    video_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    animate_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    image_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    outpaint_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    clip_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("job_id", "scene_id", name="uq_scene_jobs_job_scene"),)

    job: Mapped[GenerationJob] = relationship(back_populates="scene_jobs")

"""Per-scene job tracking, injected into rendering.

pipeline/rendering.py is pure media work — it resolves paths, shells out to
FFmpeg, and knows nothing about PostgreSQL. Recording each scene's status
there would have dragged the database into it, so rendering takes a tracker
object the same way it already takes an on_progress callback: the CLI and
tests pass the no-op, the pipeline passes the database-backed one.

The tracker also answers "has this scene already been rendered?", which is
what makes a resumed run skip the scenes a previous attempt finished
instead of spending another eight minutes each on them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pipeline import pet_repo


class SceneTracker(Protocol):
    """What pipeline/rendering.py needs from a scene tracker."""

    def reusable_clip(self, scene_id: int) -> str | None:
        """Path of a clip a previous attempt already finished for this
        scene, or None if it has to be rendered."""

    def start_scene(
        self,
        scene_id: int,
        *,
        visual_source: str | None,
        video_provider: str | None,
        animate_prompt: str | None,
        image_provider: str | None,
        background_mode: str | None,
        background_prompt: str | None,
        props: list[dict] | None = None,
    ) -> None: ...

    def record_identity(self, scene_id: int, identity_check: dict) -> None:
        """Store what a vision model saw in this shot's generated picture."""

    def finish_scene(self, scene_id: int, clip_path: str) -> None: ...

    def fail_scene(self, scene_id: int, error: str) -> None: ...


class NoopSceneTracker:
    """Default: render everything, record nothing. Keeps render_script()
    usable on its own (tests, and any caller without a job row)."""

    def reusable_clip(self, scene_id: int) -> str | None:
        return None

    def start_scene(
        self,
        scene_id: int,
        *,
        visual_source: str | None,
        video_provider: str | None,
        animate_prompt: str | None,
        image_provider: str | None,
        background_mode: str | None,
        background_prompt: str | None,
        props: list[dict] | None = None,
    ) -> None: ...

    def record_identity(self, scene_id: int, identity_check: dict) -> None: ...

    def finish_scene(self, scene_id: int, clip_path: str) -> None: ...

    def fail_scene(self, scene_id: int, error: str) -> None: ...


class DatabaseSceneTracker:
    """Records each scene against its GenerationJob and lets a resumed run
    reuse the clips an earlier attempt finished."""

    def __init__(self, job_id: int, *, resume: bool = False):
        self.job_id = job_id
        # A fresh run renders every scene even if rows somehow exist; only a
        # deliberate resume reuses previous output.
        self.resume = resume

    def reusable_clip(self, scene_id: int) -> str | None:
        if not self.resume:
            return None
        clip_path = pet_repo.get_finished_scene_clip(self.job_id, scene_id)
        # The row says it finished, but work_dir lives on disk and can be
        # deleted independently — re-render rather than fail at concat time
        # on a path that is no longer there.
        if clip_path is None or not Path(clip_path).exists():
            return None
        return clip_path

    def start_scene(
        self,
        scene_id: int,
        *,
        visual_source: str | None,
        video_provider: str | None,
        animate_prompt: str | None,
        image_provider: str | None,
        background_mode: str | None,
        background_prompt: str | None,
        props: list[dict] | None = None,
    ) -> None:
        pet_repo.start_scene_job(
            self.job_id,
            scene_id,
            visual_source=visual_source,
            video_provider=video_provider,
            animate_prompt=animate_prompt,
            image_provider=image_provider,
            background_mode=background_mode,
            background_prompt=background_prompt,
            props=props,
        )

    def record_identity(self, scene_id: int, identity_check: dict) -> None:
        pet_repo.record_scene_identity(self.job_id, scene_id, identity_check)

    def finish_scene(self, scene_id: int, clip_path: str) -> None:
        pet_repo.finish_scene_job(self.job_id, scene_id, clip_path=clip_path)

    def fail_scene(self, scene_id: int, error: str) -> None:
        pet_repo.fail_scene_job(self.job_id, scene_id, error)

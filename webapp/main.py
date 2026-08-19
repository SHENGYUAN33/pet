"""Minimal FastAPI backend for reviewing/generating pet adoption videos.

Deliberately simple per docs/architecture.md's staged roadmap: this is not
the eventual React/Next.js 人工審核 UI, just enough of a web surface to click
through the existing pipeline.run / pipeline.regen / pipeline.pet_repo
functions instead of the CLI. Business logic stays in pipeline/ — this
module is a thin HTTP wrapper, nothing here re-implements generation logic.

Generation runs on a background thread (webapp/tasks.py) and the request
returns a task_id immediately, because the web UI is the only interface
non-technical reviewers have and an Image-to-Video run can take minutes. It
is still not the persisted async Job state machine of
docs/architecture.md §10 — task state lives in this process's memory.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from pipeline import config
from pipeline.pet_repo import get_generation_job, get_pet, list_generation_jobs, list_pets, save_pet
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback
from pipeline.regen import regenerate_scene
from pipeline.run import generate_video
from webapp import tasks

app = FastAPI(title="Pet Adoption Video — Review Tool")

STATIC_DIR = Path(__file__).parent / "static"


class ImportPathRequest(BaseModel):
    path: str


def _resolve_profile_path(raw_path: str) -> Path:
    """Restrict imports to storage/profiles/ so this endpoint can't be used to
    read arbitrary files off the host filesystem (raw_path is user-controlled
    free text from the web form)."""
    candidate = (config.PROFILES_DIR / raw_path).resolve()
    profiles_dir = config.PROFILES_DIR.resolve()
    if profiles_dir not in candidate.parents and candidate != profiles_dir:
        raise HTTPException(
            status_code=400,
            detail=f"Path must be inside {profiles_dir} — got {raw_path!r}",
        )
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {candidate}")
    return candidate


@app.get("/api/profile-files")
def api_list_profile_files():
    """Filenames available under storage/profiles/, so the import form can be a
    dropdown instead of asking a non-technical reviewer to type a path."""
    profiles_dir = config.PROFILES_DIR
    if not profiles_dir.is_dir():
        return []
    return sorted(p.name for p in profiles_dir.glob("*.json") if p.is_file())


# Media a shelter volunteer can plausibly upload through the review UI. Kept as
# an allowlist (rather than blocking a few known-bad extensions) so nothing
# executable can ever land in storage/assets/ — the pipeline only ever feeds
# these to FFmpeg/XTTS.
ASSET_KIND_BY_SUFFIX = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".webp": "photo",
    ".bmp": "photo",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".mkv": "video",
    ".avi": "video",
    ".webm": "video",
    ".wav": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".ogg": "audio",
    ".flac": "audio",
}
MAX_ASSET_BYTES = 500 * 1024 * 1024
# Module-level singleton: FastAPI wants the marker as a default, ruff (B008)
# wants no call in a default.
_UPLOADED_FILE = File(...)
_SAFE_PET_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\-一-鿿]+")


def _pet_asset_dir(pet_id: str, create: bool = False) -> Path:
    """storage/assets/<pet_id>/ for a pet that exists in the DB.

    pet_id lands here straight off the URL and is used as a path component, so
    it is both charset-checked and confirmed to be a known pet before any
    filesystem access — a reviewer cannot address a directory outside
    storage/assets/ through this endpoint.
    """
    directory = (config.ASSETS_DIR / pet_id).resolve()
    # Charset check plus containment check: the first rejects separators and
    # drive letters, the second still catches anything (".." and friends) that
    # passes the charset but escapes storage/assets/.
    if not _SAFE_PET_ID.match(pet_id) or config.ASSETS_DIR.resolve() not in directory.parents:
        raise HTTPException(status_code=400, detail=f"Invalid pet_id: {pet_id!r}")
    if get_pet(pet_id) is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _asset_entry(path: Path, pet_id: str) -> dict:
    return {
        "filename": path.name,
        # Relative POSIX path is what pipeline.run/regen expect for
        # voice_sample/music_track, and what PetProfile.media.assets[].url uses.
        "path": f"storage/assets/{pet_id}/{path.name}",
        "kind": ASSET_KIND_BY_SUFFIX.get(path.suffix.lower(), "other"),
        "size_bytes": path.stat().st_size,
    }


def _unique_target(directory: Path, filename: str) -> Path:
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = directory / filename
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


@app.get("/api/pets/{pet_id}/assets")
def api_list_assets(pet_id: str):
    directory = _pet_asset_dir(pet_id)
    if not directory.is_dir():
        return []
    return [
        _asset_entry(p, pet_id)
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in ASSET_KIND_BY_SUFFIX
    ]


@app.post("/api/pets/{pet_id}/assets")
def api_upload_asset(pet_id: str, file: UploadFile = _UPLOADED_FILE):
    """Take a file the reviewer picked in their OS file dialog and store it
    under storage/assets/<pet_id>/, returning the relative path the rest of the
    pipeline uses. The browser never exposes the real local path of a picked
    file, so uploading is what makes 'pick a file on my computer' usable at
    all — the UI then works with the stored copy."""
    directory = _pet_asset_dir(pet_id, create=True)

    raw_name = Path(file.filename or "").name
    suffix = Path(raw_name).suffix.lower()
    if suffix not in ASSET_KIND_BY_SUFFIX:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支援的檔案格式 {suffix or raw_name!r}，"
                f"可用：{', '.join(sorted(ASSET_KIND_BY_SUFFIX))}"
            ),
        )
    safe_stem = _UNSAFE_FILENAME_CHARS.sub("_", Path(raw_name).stem).strip("._") or "asset"
    target = _unique_target(directory, f"{safe_stem}{suffix}")

    try:
        with open(target, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
            if out.tell() > MAX_ASSET_BYTES:
                raise ValueError(f"檔案超過上限 {MAX_ASSET_BYTES // (1024 * 1024)}MB")
    except (OSError, ValueError) as e:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"上傳失敗：{e}") from e
    finally:
        file.file.close()

    return _asset_entry(target, pet_id)


class GenerateRequest(BaseModel):
    style: str = "cute"
    duration: int = 30
    voice_sample: str | None = None
    music_track: str | None = None
    animate_scenes: list[int] | None = None
    video_provider: str = "svd"
    animate_prompt: str | None = None


class RegenerateSceneRequest(BaseModel):
    scene_id: int
    visual_source: str | None = None
    subtitle: str | None = None
    narration: str | None = None
    voice_sample: str | None = None
    music_track: str | None = None
    animate: bool = False
    video_provider: str = "svd"
    animate_prompt: str | None = None


@app.get("/api/pets")
def api_list_pets():
    return [{"pet_id": p.pet_id, "name": p.name, "species": p.species} for p in list_pets()]


@app.get("/api/pets/{pet_id}")
def api_get_pet(pet_id: str):
    profile = get_pet(pet_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")
    return {
        "profile": profile.model_dump(mode="json"),
        "jobs": list_generation_jobs(pet_id),
    }


@app.post("/api/pets/import-path")
def api_import_by_path(req: ImportPathRequest):
    resolved = _resolve_profile_path(req.path)
    try:
        profile = PetProfile.load(resolved)
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid Pet Profile: {e}") from e
    save_pet(profile)
    return {"pet_id": profile.pet_id, "name": profile.name}


@app.put("/api/pets/{pet_id}/profile")
def api_save_profile(pet_id: str, profile: PetProfile):
    if profile.pet_id != pet_id:
        raise HTTPException(
            status_code=400,
            detail=f"pet_id in URL ({pet_id!r}) must match profile.pet_id ({profile.pet_id!r})",
        )
    save_pet(profile)
    return {"pet_id": profile.pet_id, "name": profile.name}


@app.post("/api/pets/{pet_id}/generate", status_code=202)
def api_generate(pet_id: str, req: GenerateRequest):
    """Start a generation run and return its task_id — poll /api/tasks/{id}
    for progress. Inputs that are already knowable as wrong (unknown pet) are
    rejected here rather than inside the task, so the reviewer gets an
    immediate error instead of a task that fails a minute later."""
    if get_pet(pet_id) is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")

    def work(on_progress: ProgressCallback) -> dict:
        output_path, job_id = generate_video(
            pet_id=pet_id,
            voice_sample=req.voice_sample,
            music_track=req.music_track,
            style=req.style,
            duration=req.duration,
            animate_scenes=set(req.animate_scenes) if req.animate_scenes else None,
            video_provider=req.video_provider,
            animate_prompt=req.animate_prompt,
            on_progress=on_progress,
        )
        return {"output_path": output_path, "job_id": job_id}

    return _start(kind="generate", pet_id=pet_id, label="產生新影片", work=work)


@app.post("/api/jobs/{job_id}/regenerate-scene", status_code=202)
def api_regenerate_scene(job_id: int, req: RegenerateSceneRequest):
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")

    def work(on_progress: ProgressCallback) -> dict:
        output_path, new_job_id = regenerate_scene(
            job_id,
            req.scene_id,
            visual_source=req.visual_source,
            subtitle=req.subtitle,
            narration=req.narration,
            voice_sample=req.voice_sample,
            music_track=req.music_track,
            animate=req.animate,
            video_provider=req.video_provider,
            animate_prompt=req.animate_prompt,
            on_progress=on_progress,
        )
        return {"output_path": output_path, "job_id": new_job_id}

    return _start(
        kind="regenerate",
        pet_id=job["pet_id"],
        label=f"重生 Job {job_id} 的鏡頭 {req.scene_id}",
        work=work,
    )


def _start(**kwargs) -> dict:
    try:
        return tasks.start_task(**kwargs)
    except tasks.TaskBusyError as e:
        # One at a time: generation saturates the GPU/CPU, so a second
        # concurrent run would slow both down rather than finish sooner.
        raise HTTPException(
            status_code=409,
            detail=f"目前正在執行「{e}」，請等它跑完再送出下一個。",
        ) from e


@app.get("/api/tasks")
def api_list_tasks(pet_id: str | None = None):
    return tasks.list_tasks(pet_id)


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str):
    task = tasks.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task found with id {task_id!r}")
    return task


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    """Full job record including script_json, so the review UI can list a
    job's scenes (id / purpose / subtitle) for the 單鏡頭重生 panel instead of
    making the reviewer type a raw scene id."""
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    return job


@app.get("/api/jobs/{job_id}/video")
def api_get_job_video(job_id: int):
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    path = Path(job["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Output file missing: {path}")
    return FileResponse(path, media_type="video/mp4")


# Uploaded media, read-only, so the profile editor can show photo thumbnails
# instead of bare filenames. Serves storage/assets/ only — generated output
# still goes through /api/jobs/{id}/video.
config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.ASSETS_DIR), name="media")

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

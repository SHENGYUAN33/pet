"""Minimal FastAPI backend for reviewing/generating pet adoption videos.

Deliberately simple per docs/architecture.md's staged roadmap: this is not
the eventual React/Next.js 人工審核 UI, just enough of a web surface to click
through the existing pipeline.run / pipeline.regen / pipeline.pet_repo
functions instead of the CLI. Business logic stays in pipeline/ — this
module is a thin HTTP wrapper, nothing here re-implements generation logic.

Generation runs on a background thread (webapp/tasks.py) and the request
returns a task_id immediately, because the web UI is the only interface
non-technical reviewers have and an Image-to-Video run can take minutes.

Live task state (progress percentage, current step) is in-process by
design: it describes a running thread, and a restart kills the thread, so
persisting the percentage would only preserve a number about work that is
no longer happening. What does survive a restart is the GenerationJob row
and its per-scene rows, which is what the reaper below and pipeline.resume
build on.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from pipeline import config, decoration
from pipeline.background import BackgroundMode
from pipeline.editing import PHOTO_EXTENSIONS, build_scene_clip
from pipeline.pet_repo import (
    approve_generation_job,
    cleanup_generation_job,
    cleanup_old_generation_jobs,
    get_generation_job,
    get_pet,
    list_generation_jobs,
    list_pets,
    list_scene_jobs,
    reap_interrupted_jobs,
    reject_generation_job,
    save_pet,
)
from pipeline.planning import ScenePlan, plan_scenes
from pipeline.profile import PetProfile
from pipeline.progress import ProgressCallback
from pipeline.regen import regenerate_scene
from pipeline.review import publication_blockers
from pipeline.run import generate_video, resume_generation_job
from webapp import tasks

INTERRUPTED_REASON = "伺服器重新啟動，這次生成被中斷（可從已完成的鏡頭續跑）"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Close out jobs the previous process was running when it died.

    Without this a killed run leaves a row stuck at RUNNING and the review
    UI shows "生成中" forever for work that nothing is doing. Marked failed
    instead, they surface as resumable — the scenes they finished are still
    on disk."""
    reaped = reap_interrupted_jobs(INTERRUPTED_REASON)
    if reaped:
        print(f"[startup] marked {len(reaped)} interrupted job(s) as failed: {reaped}")
    yield


app = FastAPI(title="Pet Adoption Video — Review Tool", lifespan=lifespan)

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
    #: Scenes to overrule the script on. Left empty — the normal case — every
    #: shot gets whatever background its script asked for, which is where the
    #: story's own sequence of places lives (pipeline/background.py).
    background_scenes: list[int] | None = None
    background_mode: BackgroundMode = BackgroundMode.EXTEND
    background_prompt: str | None = None
    image_provider: str = "comfy"
    #: The reviewer's own look, when they picked one. None keeps the colour
    #: the script's style would have chosen.
    accent_colour: str | None = None
    border_width: int | None = None
    recap_unused_assets: bool = False


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
    generate_background: bool = False
    background_mode: BackgroundMode = BackgroundMode.EXTEND
    background_prompt: str | None = None
    image_provider: str = "comfy"
    accent_colour: str | None = None
    border_width: int | None = None


@app.get("/api/pets/{pet_id}/decor-preview")
def api_decor_preview(
    pet_id: str,
    style: str = "cute",
    accent_colour: str | None = None,
    border_width: int | None = None,
):
    """One still frame dressed exactly as a real shot would be.

    Tuning a border by rendering a whole video means a three-minute wait per
    guess, which is not tuning. This runs the same build_scene_clip the
    pipeline uses — a fraction of a second of it — and hands back the middle
    frame, so what the reviewer is looking at cannot drift from what they
    will get.
    """
    profile = get_pet(pet_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")

    photo = next(
        (a for a in profile.media.assets if Path(a.url).suffix.lower() in PHOTO_EXTENSIONS),
        None,
    )
    if photo is None:
        raise HTTPException(status_code=400, detail="這隻寵物還沒有照片，沒有東西可以預覽")
    source = config.ASSETS_DIR / pet_id / Path(photo.url).name
    if not source.exists():
        raise HTTPException(status_code=400, detail=f"素材檔案不存在：{source.name}")

    preview_dir = Path(tempfile.mkdtemp(prefix="decor-preview-"))
    try:
        clip = preview_dir / "preview.mp4"
        build_scene_clip(
            visual_path=str(source),
            duration=0.2,
            subtitle_text="字幕會長這樣",
            output_path=str(clip),
            accent_colour=decoration.resolve_accent(style, accent_colour),
            border_width=border_width,
            info_card_text=decoration.identity_line(profile),
        )
        frame = preview_dir / "preview.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", str(frame)],
            check=True,
        )
        # Read it back before the directory goes: FileResponse would stream
        # after this function has returned and the cleanup has run.
        return Response(content=frame.read_bytes(), media_type="image/png")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"預覽產生失敗：{e}") from e
    finally:
        shutil.rmtree(preview_dir, ignore_errors=True)


@app.get("/api/pets/{pet_id}/scene-plan")
def api_scene_plan(pet_id: str, duration: int = 30) -> ScenePlan:
    """How many of this pet's assets a video of this length can show.

    The generate form asks as the length changes, so "you have 13 assets and
    this video will use 7 of them" is on screen before the run starts rather
    than something to work out from the output afterwards."""
    profile = get_pet(pet_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")
    return plan_scenes(duration, len(profile.media.assets))


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
            background_scenes=set(req.background_scenes) if req.background_scenes else None,
            background_mode=req.background_mode,
            background_prompt=req.background_prompt,
            image_provider=req.image_provider,
            accent_colour=req.accent_colour,
            border_width=req.border_width,
            recap_unused_assets=req.recap_unused_assets,
            on_progress=on_progress,
        )
        return {"output_path": output_path, "job_id": job_id}

    return _start(kind="generate", pet_id=pet_id, label="產生新影片", work=work)


@app.delete("/api/pets/{pet_id}/job-files")
def api_cleanup_old_jobs(pet_id: str, keep: int = 3):
    """Clean up every version except the newest `keep` — the batch form of
    the per-version cleanup, for clearing out a debugging session's pile."""
    if get_pet(pet_id) is None:
        raise HTTPException(status_code=404, detail=f"No pet found with id {pet_id!r}")
    if keep < 0:
        raise HTTPException(status_code=400, detail="keep must not be negative")
    return cleanup_old_generation_jobs(pet_id, keep=keep)


@app.delete("/api/jobs/{job_id}/files")
def api_cleanup_job(job_id: int):
    """Delete a version's rendered files, keeping its generation record.

    Named for what it removes: the row survives, because the provider,
    prompt and script that produced the video are what the project requires
    kept (CLAUDE.md 開發規範) — the scene clips are not."""
    try:
        return cleanup_generation_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/jobs/{job_id}/regenerate-scene", status_code=202)
def api_regenerate_scene(job_id: int, req: RegenerateSceneRequest):
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    # Motion guidance with animation switched off means the shot renders as
    # Ken Burns and the description is thrown away — which reads afterwards
    # as "I2V changed nothing" rather than as the contradiction it is.
    if req.animate_prompt and not req.animate:
        raise HTTPException(
            status_code=400,
            detail="填了動作描述，但沒有啟用動態化 — 請選一個影片生成模型，否則這顆鏡頭只會做照片運鏡",
        )
    # Same class of contradiction: a described setting that never gets
    # generated reads afterwards as "the background did nothing".
    if req.background_prompt and not req.generate_background:
        raise HTTPException(
            status_code=400,
            detail="填了背景描述，但沒有啟用背景生成 — 勾選「重新生成背景」，否則這顆鏡頭會照原本的畫面渲染",
        )

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
            generate_background=req.generate_background,
            background_mode=req.background_mode,
            background_prompt=req.background_prompt,
            image_provider=req.image_provider,
            accent_colour=req.accent_colour,
            border_width=req.border_width,
            on_progress=on_progress,
        )
        return {"output_path": output_path, "job_id": new_job_id}

    return _start(
        kind="regenerate",
        pet_id=job["pet_id"],
        label=f"重生 Job {job_id} 的鏡頭 {req.scene_id}",
        work=work,
    )


@app.post("/api/jobs/{job_id}/resume", status_code=202)
def api_resume_job(job_id: int):
    """Continue an unfinished run, reusing the scenes it already rendered.

    Worth its own endpoint rather than "just generate again": a Wan2.2 scene
    costs about eight minutes, so re-rendering the scenes that already
    succeeded is the expensive mistake this avoids.

    Takes no body — everything that shapes the output was stored on the job
    when it started, and letting a caller override it here would finish a
    different video from the one that was interrupted."""
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    if job["status"] == "done":
        raise HTTPException(status_code=409, detail=f"Job {job_id} 已經完成了，沒有東西可以續跑。")
    if not job["script_json"] or not job["work_dir"]:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} 在腳本產生前就失敗了，請直接重新產生一支新影片。",
        )

    def work(on_progress: ProgressCallback) -> dict:
        output_path = resume_generation_job(job_id, on_progress=on_progress)
        return {"output_path": output_path, "job_id": job_id}

    return _start(kind="resume", pet_id=job["pet_id"], label=f"續跑 Job {job_id}", work=work)


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


class ReviewRequest(BaseModel):
    note: str | None = None


@app.get("/api/jobs/{job_id}/blockers")
def api_job_blockers(job_id: int):
    """Why this version cannot be approved yet, or an empty list.

    The review panel asks before offering the approve button, so a reviewer
    sees the reason rather than a button that refuses when pressed."""
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    return {"blockers": publication_blockers(job)}


@app.post("/api/jobs/{job_id}/approve")
def api_approve_job(job_id: int, req: ReviewRequest):
    """Record that a person approved this version.

    The refusal lives in pipeline/pet_repo.py, not here: hiding the button
    would be a UI courtesy, and this is a rule."""
    try:
        return approve_generation_job(job_id, note=req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/jobs/{job_id}/reject")
def api_reject_job(job_id: int, req: ReviewRequest):
    """Record that a person turned this version down, with the reason."""
    try:
        return reject_generation_job(job_id, note=req.note or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    """Full job record including script_json, so the review UI can list a
    job's scenes (id / purpose / subtitle) for the 單鏡頭重生 panel instead of
    making the reviewer type a raw scene id."""
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    # Per-scene status/provenance, so the reviewer can see which shot failed
    # and which provider made each one.
    job["scene_jobs"] = list_scene_jobs(job_id)
    return job


@app.get("/api/jobs/{job_id}/video")
def api_get_job_video(job_id: int):
    job = get_generation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No generation job found with id {job_id}")
    if job["output_path"] is None:
        # A job that is still running or that failed has no file yet; say so
        # rather than 500ing on a None path.
        raise HTTPException(
            status_code=409,
            detail=f"這個版本還沒有影片（狀態：{job['status']}）"
            + (f"：{job['error']}" if job["error"] else ""),
        )
    if job["cleaned_at"]:
        # Deliberately deleted, not lost — say which, so this doesn't read as
        # a bug in the pipeline.
        raise HTTPException(
            status_code=410,
            detail="這個版本的檔案已被清理（生成紀錄仍保留），無法播放",
        )
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

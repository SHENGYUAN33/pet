"""Minimal FastAPI backend for reviewing/generating pet adoption videos.

Deliberately simple per docs/architecture.md's staged roadmap: this is not
the eventual React/Next.js 人工審核 UI, just enough of a web surface to click
through the existing pipeline.run / pipeline.regen / pipeline.pet_repo
functions instead of the CLI. Business logic stays in pipeline/ — this
module is a thin HTTP wrapper, nothing here re-implements generation logic.

Generation requests are handled synchronously (the request blocks until
FFmpeg/TTS/the LLM finish, tens of seconds). There is no async Job state
machine yet (see CLAUDE.md) — a real multi-user deployment would need a
background task queue, deliberately out of scope for this slice.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from pipeline import config
from pipeline.pet_repo import get_generation_job, get_pet, list_generation_jobs, list_pets, save_pet
from pipeline.profile import PetProfile
from pipeline.regen import regenerate_scene
from pipeline.run import generate_video

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


@app.post("/api/pets/{pet_id}/generate")
def api_generate(pet_id: str, req: GenerateRequest):
    try:
        output_path, job_id = generate_video(
            pet_id=pet_id,
            voice_sample=req.voice_sample,
            music_track=req.music_track,
            style=req.style,
            duration=req.duration,
            animate_scenes=set(req.animate_scenes) if req.animate_scenes else None,
            video_provider=req.video_provider,
            animate_prompt=req.animate_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"output_path": output_path, "job_id": job_id}


@app.post("/api/jobs/{job_id}/regenerate-scene")
def api_regenerate_scene(job_id: int, req: RegenerateSceneRequest):
    try:
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
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"output_path": output_path, "job_id": new_job_id}


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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline.profile import PetProfile
from pipeline.rendering import render_script
from providers.base import VideoGenerationProvider

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


class FakeVideoProvider(VideoGenerationProvider):
    """Stands in for SVD/CogVideoX in tests: a real diffusion model needs a
    GPU and multi-GB weights, so this only verifies render_script's plumbing
    (does it call the provider, feed the output into build_scene_clip,
    produce the right final duration) — not generation quality."""

    def animate_image(
        self,
        image_path: str,
        *,
        duration_seconds: float,
        output_path: str,
        prompt: str | None = None,
    ) -> str:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=1024x576:d=2:r=8",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
        return output_path


def _probe_duration(path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


@pytest.fixture
def photo_profile(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=640x480:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )
    profile = PetProfile.model_validate(
        {
            "pet_id": "PET-I2V-TEST",
            "name": "測試貓",
            "species": "cat",
            "sex": "male",
            "age": "1歲",
            "size": "medium",
            "health_status": {"vaccinated": True, "neutered": True, "microchipped": True},
            "personality_tags": {
                "appeal": [],
                "lifestyle_fit": [],
                "care_needs": [],
                "restrictions": [],
            },
            "adoption_requirements": [],
            "contact_url": "https://example.org/adopt/test",
            "media": {"assets": [{"asset_id": "IMG-001", "type": "photo", "url": str(photo)}]},
            "identity_card": {},
        }
    )
    return profile


def _script_with_one_photo_scene(duration: float = 4.0) -> dict:
    return {
        "style": "cute",
        "scenes": [
            {
                "scene_id": 1,
                "start": 0,
                "end": duration,
                "visual_source": "IMG-001",
                "subtitle": "測試",
            }
        ],
    }


def test_render_script_animates_flagged_photo_scene(photo_profile, tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.rendering.get_video_provider", lambda name: FakeVideoProvider())

    work_dir = tmp_path / "work"
    script = _script_with_one_photo_scene(duration=4.0)

    final_path = render_script(photo_profile, script, work_dir, animate_scenes={1})

    assert (work_dir / "scene_1_i2v.mp4").exists(), "I2V provider should have been invoked"
    assert abs(_probe_duration(final_path) - 4.0) < 0.2


def test_render_script_without_animate_scenes_uses_ken_burns(photo_profile, tmp_path):
    work_dir = tmp_path / "work"
    script = _script_with_one_photo_scene(duration=3.0)

    final_path = render_script(photo_profile, script, work_dir)

    assert not (work_dir / "scene_1_i2v.mp4").exists()
    assert abs(_probe_duration(final_path) - 3.0) < 0.2

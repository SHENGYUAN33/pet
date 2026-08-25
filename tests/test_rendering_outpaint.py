"""render_script's generated-background plumbing.

Companion to test_rendering_i2v.py, and for the same reason: a real SDXL pass
needs a GPU and a multi-gigabyte checkpoint, so this checks that the right
scenes reach the provider, that the result is what gets rendered, and that a
second attempt doesn't pay for the same background twice — not what the
picture looks like.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline import config
from pipeline.profile import PetProfile
from pipeline.rendering import render_script
from providers.base import ImageEditingProvider

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


class FakeOutpaintProvider(ImageEditingProvider):
    """Writes a frame-shaped picture and records what it was asked for."""

    def __init__(self):
        self.calls = []

    def outpaint_to_frame(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        self.calls.append({"image_path": image_path, "prompt": prompt})
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=green:s={target_width}x{target_height}:d=1",
                "-frames:v",
                "1",
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
    return PetProfile.model_validate(
        {
            "pet_id": "PET-OUTPAINT-TEST",
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


def _script_with_one_photo_scene(duration: float = 3.0) -> dict:
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


def test_flagged_scene_is_rendered_from_the_generated_background(
    photo_profile, tmp_path, monkeypatch
):
    provider = FakeOutpaintProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    work_dir = tmp_path / "work"
    final_path = render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        work_dir,
        outpaint_scenes={1},
        outpaint_prompt="溫暖的客廳，午後陽光",
    )

    assert (work_dir / "scene_1_bg.png").exists()
    assert [call["prompt"] for call in provider.calls] == ["溫暖的客廳，午後陽光"]
    assert abs(_probe_duration(final_path) - 3.0) < 0.2


def test_a_background_already_on_disk_is_not_generated_again(photo_profile, tmp_path, monkeypatch):
    """The point of caching it under work_dir: a run that died after the
    background but before the (much slower) animation must not repeat a
    sampling pass it already paid for."""
    provider = FakeOutpaintProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    work_dir = tmp_path / "work"
    script = _script_with_one_photo_scene()
    render_script(photo_profile, script, work_dir, outpaint_scenes={1})
    render_script(photo_profile, script, work_dir, outpaint_scenes={1})

    assert len(provider.calls) == 1


def test_nothing_reaches_the_provider_when_no_scene_asked_for_a_background(
    photo_profile, tmp_path, monkeypatch
):
    class ExplodingProvider(FakeOutpaintProvider):
        def preflight(self) -> None:
            raise AssertionError("preflight should not run without outpainted scenes")

        def outpaint_to_frame(self, *args, **kwargs) -> str:
            raise AssertionError("no scene asked for a generated background")

    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: ExplodingProvider())

    work_dir = tmp_path / "work"
    render_script(photo_profile, _script_with_one_photo_scene(), work_dir)

    assert not (work_dir / "scene_1_bg.png").exists()


def test_an_unavailable_provider_surfaces_before_the_narration_pass(
    photo_profile, tmp_path, monkeypatch
):
    """Same contract as the I2V preflight: the whole point of checking is to
    not spend the TTS pass first."""

    class UnreachableProvider(FakeOutpaintProvider):
        def preflight(self) -> None:
            raise RuntimeError("ComfyUI has no checkpoint named 'sd_xl_base_1.0.safetensors'")

    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: UnreachableProvider())

    work_dir = tmp_path / "work"
    with pytest.raises(RuntimeError, match="no checkpoint"):
        render_script(photo_profile, _script_with_one_photo_scene(), work_dir, outpaint_scenes={1})

    assert not (work_dir / "audio").exists(), "should fail before generating scene audio"


def test_a_recap_shot_is_left_alone_even_when_its_id_is_listed(
    photo_profile, tmp_path, monkeypatch
):
    """The recap cuts through several assets in one shot; there is no single
    still to extend, so listing its id is ignored rather than an error."""
    provider = FakeOutpaintProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    photo = photo_profile.media.assets[0].url
    photo_profile.media.assets.append(
        photo_profile.media.assets[0].model_copy(update={"asset_id": "IMG-002", "url": photo})
    )
    script = {
        "style": "cute",
        "scenes": [
            {
                "scene_id": 1,
                "start": 0,
                "end": 3,
                "visual_source": "IMG-001",
                "subtitle": "故事",
            },
            {
                "scene_id": 2,
                "start": 3,
                "end": 5,
                "purpose": "recap",
                "visual_sources": ["IMG-001", "IMG-002"],
                "subtitle": "點我看領養資訊",
                "narration": "",
            },
        ],
    }

    work_dir = tmp_path / "work"
    render_script(photo_profile, script, work_dir, outpaint_scenes={2})

    assert provider.calls == []
    assert not (work_dir / "scene_2_bg.png").exists()


def test_the_provider_is_asked_for_the_delivery_frames_shape(photo_profile, tmp_path, monkeypatch):
    """render_script must not invent its own target size — the frame the
    pipeline delivers at lives in config, in one place."""
    seen = {}

    class RecordingProvider(FakeOutpaintProvider):
        def outpaint_to_frame(self, image_path, *, target_width, target_height, **kwargs):
            seen["size"] = (target_width, target_height)
            return super().outpaint_to_frame(
                image_path, target_width=target_width, target_height=target_height, **kwargs
            )

    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: RecordingProvider())

    render_script(
        photo_profile, _script_with_one_photo_scene(), tmp_path / "work", outpaint_scenes={1}
    )

    assert seen["size"] == (config.OUTPAINT_WIDTH, config.OUTPAINT_HEIGHT)

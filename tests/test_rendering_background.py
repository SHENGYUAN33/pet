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
from pipeline.background import BackgroundMode
from pipeline.profile import PetProfile
from pipeline.rendering import render_script
from providers.base import ImageEditingProvider

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


class FakeBackgroundProvider(ImageEditingProvider):
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
        self.calls.append({"image_path": image_path, "prompt": prompt, "mode": "extend"})
        return self._write_frame(target_width, target_height, output_path)

    def _write_frame(self, target_width: int, target_height: int, output_path: str) -> str:
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

    def replace_background(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
        subject: str | None = None,
    ) -> str:
        """Same stand-in, recording the mode and the subject so a test can
        tell which treatment render_script asked for, and what it said to
        keep in the frame."""
        self.calls.append(
            {"image_path": image_path, "prompt": prompt, "mode": "replace", "subject": subject}
        )
        return self._write_frame(target_width, target_height, output_path)


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
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    work_dir = tmp_path / "work"
    final_path = render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        work_dir,
        background_scenes={1},
        background_prompt="溫暖的客廳，午後陽光",
    )

    assert (work_dir / "scene_1_bg.png").exists()
    assert [call["prompt"] for call in provider.calls] == ["溫暖的客廳，午後陽光"]
    assert abs(_probe_duration(final_path) - 3.0) < 0.2


def test_a_background_already_on_disk_is_not_generated_again(photo_profile, tmp_path, monkeypatch):
    """The point of caching it under work_dir: a run that died after the
    background but before the (much slower) animation must not repeat a
    sampling pass it already paid for."""
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    work_dir = tmp_path / "work"
    script = _script_with_one_photo_scene()
    render_script(photo_profile, script, work_dir, background_scenes={1})
    render_script(photo_profile, script, work_dir, background_scenes={1})

    assert len(provider.calls) == 1


def test_nothing_reaches_the_provider_when_no_scene_asked_for_a_background(
    photo_profile, tmp_path, monkeypatch
):
    class ExplodingProvider(FakeBackgroundProvider):
        def preflight(self, *, mode: str = "extend") -> None:
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

    class UnreachableProvider(FakeBackgroundProvider):
        def preflight(self, *, mode: str = "extend") -> None:
            raise RuntimeError("ComfyUI has no checkpoint named 'sd_xl_base_1.0.safetensors'")

    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: UnreachableProvider())

    work_dir = tmp_path / "work"
    with pytest.raises(RuntimeError, match="no checkpoint"):
        render_script(
            photo_profile, _script_with_one_photo_scene(), work_dir, background_scenes={1}
        )

    assert not (work_dir / "audio").exists(), "should fail before generating scene audio"


def test_a_recap_shot_is_left_alone_even_when_its_id_is_listed(
    photo_profile, tmp_path, monkeypatch
):
    """The recap cuts through several assets in one shot; there is no single
    still to extend, so listing its id is ignored rather than an error."""
    provider = FakeBackgroundProvider()
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
    render_script(photo_profile, script, work_dir, background_scenes={2})

    assert provider.calls == []
    assert not (work_dir / "scene_2_bg.png").exists()


def test_the_provider_is_asked_for_the_delivery_frames_shape(photo_profile, tmp_path, monkeypatch):
    """render_script must not invent its own target size — the frame the
    pipeline delivers at lives in config, in one place."""
    seen = {}

    class RecordingProvider(FakeBackgroundProvider):
        def outpaint_to_frame(self, image_path, *, target_width, target_height, **kwargs):
            seen["size"] = (target_width, target_height)
            return super().outpaint_to_frame(
                image_path, target_width=target_width, target_height=target_height, **kwargs
            )

    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: RecordingProvider())

    render_script(
        photo_profile, _script_with_one_photo_scene(), tmp_path / "work", background_scenes={1}
    )

    assert seen["size"] == (config.BACKGROUND_WIDTH, config.BACKGROUND_HEIGHT)


def test_replace_mode_asks_the_provider_for_a_new_setting(photo_profile, tmp_path, monkeypatch):
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        tmp_path / "work",
        background_scenes={1},
        background_mode=BackgroundMode.REPLACE,
        background_prompt="green grass in a sunny park",
    )

    assert [call["mode"] for call in provider.calls] == ["replace"]


def test_a_replaced_setting_is_labelled_as_ai_generated(photo_profile, tmp_path, monkeypatch):
    """docs/architecture.md §5 strategy C: a viewer must not be able to
    mistake an invented place for where this animal actually is. Burned in
    by rendering rather than left to the caller, so it cannot be forgotten.
    """
    monkeypatch.setattr(
        "pipeline.rendering.get_image_provider", lambda name: FakeBackgroundProvider()
    )

    work_dir = tmp_path / "work"
    render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        work_dir,
        background_scenes={1},
        background_mode=BackgroundMode.REPLACE,
    )

    disclosure = work_dir / "scene_1.disclosure.txt"
    assert disclosure.exists()
    assert disclosure.read_text(encoding="utf-8") == config.BACKGROUND_DISCLOSURE_TEXT


def test_an_extended_margin_is_not_labelled(photo_profile, tmp_path, monkeypatch):
    """Nothing the camera saw was replaced, and a label that appeared on
    every filled-in margin would stop meaning anything where it matters."""
    monkeypatch.setattr(
        "pipeline.rendering.get_image_provider", lambda name: FakeBackgroundProvider()
    )

    work_dir = tmp_path / "work"
    render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        work_dir,
        background_scenes={1},
        background_mode=BackgroundMode.EXTEND,
    )

    assert not (work_dir / "scene_1.disclosure.txt").exists()


def test_an_untouched_scene_is_not_labelled(photo_profile, tmp_path):
    """Only the shots that actually got a generated setting carry the label
    — including in a run where some other scene did."""
    work_dir = tmp_path / "work"
    render_script(photo_profile, _script_with_one_photo_scene(), work_dir)

    assert not (work_dir / "scene_1.disclosure.txt").exists()


def test_replace_is_told_which_animal_to_keep(photo_profile, tmp_path, monkeypatch):
    """The profile knows the species and the provider does not, so rendering
    has to pass it through — otherwise a dog's photo is segmented by whatever
    the provider guesses at."""
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    render_script(
        photo_profile,
        _script_with_one_photo_scene(),
        tmp_path / "work",
        background_scenes={1},
        background_mode=BackgroundMode.REPLACE,
    )

    assert [call["subject"] for call in provider.calls] == [photo_profile.species]


def test_each_scene_gets_the_treatment_its_script_asked_for(photo_profile, tmp_path, monkeypatch):
    """The whole point of moving backgrounds into the script: one video can
    keep one shot as photographed, extend another and replace a third."""
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    script = {
        "style": "cute",
        "art_direction": "warm afternoon light",
        "scenes": [
            {
                "scene_id": 1,
                "start": 0,
                "end": 3,
                "visual_source": "IMG-001",
                "subtitle": "一",
                "background": {"mode": "keep", "prompt": None},
            },
            {
                "scene_id": 2,
                "start": 3,
                "end": 6,
                "visual_source": "IMG-001",
                "subtitle": "二",
                "background": {"mode": "extend", "prompt": "a cat indoors"},
            },
            {
                "scene_id": 3,
                "start": 6,
                "end": 9,
                "visual_source": "IMG-001",
                "subtitle": "三",
                "background": {"mode": "replace", "prompt": "a sunny park"},
            },
        ],
    }

    work_dir = tmp_path / "work"
    render_script(photo_profile, script, work_dir)

    assert [call["mode"] for call in provider.calls] == ["extend", "replace"]
    assert not (work_dir / "scene_1_bg.png").exists(), "keep shows the photograph"

    # The film-wide look rides along with each shot's own description.
    assert provider.calls[0]["prompt"] == "a cat indoors, warm afternoon light"
    assert provider.calls[1]["prompt"] == "a sunny park, warm afternoon light"

    # Only the invented setting is labelled.
    assert not (work_dir / "scene_2.disclosure.txt").exists()
    assert (work_dir / "scene_3.disclosure.txt").exists()


def test_a_named_scene_overrides_what_the_script_chose(photo_profile, tmp_path, monkeypatch):
    """A reviewer correcting one shot has to win over the script."""
    provider = FakeBackgroundProvider()
    monkeypatch.setattr("pipeline.rendering.get_image_provider", lambda name: provider)

    script = _script_with_one_photo_scene()
    script["scenes"][0]["background"] = {"mode": "keep", "prompt": None}

    render_script(
        photo_profile,
        script,
        tmp_path / "work",
        background_scenes={1},
        background_mode=BackgroundMode.REPLACE,
        background_prompt="a sunny park",
    )

    assert [call["mode"] for call in provider.calls] == ["replace"]

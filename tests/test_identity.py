"""The identity check: does a generated shot still show this pet, and only it.

The vision model's answers are its own business; what is testable here — and
what decides whether a reviewer trusts the warnings — is how those answers
are turned into findings, and that the check can never cost anyone a video.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline import config
from pipeline.identity import IdentityCheck, _judge, check_identity, get_vlm_provider
from providers.base import VLMProvider


class FakeVLM(VLMProvider):
    """Answers with whatever a test hands it, in place of a 12B model."""

    def __init__(self, answer: str):
        self.answer = answer
        self.prompts: list[str] = []

    def inspect_image(self, image_path: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


def test_a_shot_with_the_pet_in_it_passes():
    result = _judge(
        {
            "animals_visible": 1,
            "species": "cat",
            "description": "A grey cat lying on grass.",
            "body_intact": True,
            "sits_in_the_scene": True,
        },
        species="cat",
    )

    assert result.passed
    assert result.issues == []
    assert result.summary() == "通過"


def test_a_shot_with_no_animal_is_caught():
    """The failure this exists for: the segmenter found nothing, the sampler
    repainted everything, and the adoption video shows an empty park."""
    result = _judge({"animals_visible": 0, "species": "none"}, species="cat")

    assert not result.passed
    assert any("看不到任何動物" in issue for issue in result.issues)


def test_an_extra_animal_is_caught():
    """A second cat in the generated area is an animal that does not exist —
    a claim about the pet, not just an ugly picture."""
    result = _judge({"animals_visible": 3, "species": "cat"}, species="cat")

    assert not result.passed
    assert any("3 隻動物" in issue for issue in result.issues)


def test_the_wrong_species_is_caught():
    result = _judge({"animals_visible": 1, "species": "dog"}, species="cat")

    assert not result.passed
    assert any("dog" in issue and "cat" in issue for issue in result.issues)


def test_appearance_judgements_are_worded_as_something_to_look_at():
    """Counting animals is close to arithmetic and the model is reliable at
    it; whether a cut-out looks pasted on is a judgement, and stating it as
    a verdict would teach reviewers to ignore the warnings."""
    result = _judge(
        {"animals_visible": 1, "species": "cat", "body_intact": False, "sits_in_the_scene": False},
        species="cat",
    )

    assert not result.passed
    assert all("請確認" in issue for issue in result.issues)


def test_appearance_judgements_are_dropped_when_there_is_no_animal():
    """With nothing in the picture they are meaningless, and they would bury
    the finding that actually matters."""
    result = _judge(
        {"animals_visible": 0, "species": "none", "body_intact": False, "sits_in_the_scene": False},
        species="cat",
    )

    assert len(result.issues) == 1


def test_an_uncountable_answer_asks_for_a_human():
    result = _judge({"animals_visible": "several", "species": "cat"}, species="cat")

    assert not result.passed
    assert result.animals_visible is None
    assert any("人工確認" in issue for issue in result.issues)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_a_still_is_inspected_without_being_re_encoded(tmp_path):
    photo = tmp_path / "shot.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=64x64:d=1",
            "-frames:v",
            "1",
            str(photo),
        ],
        check=True,
        capture_output=True,
    )
    vlm = FakeVLM('{"animals_visible": 1, "species": "cat", "description": "a cat"}')

    result = check_identity(str(photo), vlm, species="cat", work_dir=tmp_path, scene_id=1)

    assert result.passed
    assert not (tmp_path / "scene_1_identity.png").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_a_clip_is_sampled_at_its_middle(tmp_path):
    """An Image-to-Video clip starts from the source photo and drifts, so the
    opening frame is the one place the animal is guaranteed to look right —
    checking it would miss exactly what this is for."""
    clip = tmp_path / "shot.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=4:r=8", str(clip)],
        check=True,
        capture_output=True,
    )
    vlm = FakeVLM('{"animals_visible": 1, "species": "cat", "description": "a cat"}')

    result = check_identity(str(clip), vlm, species="cat", work_dir=tmp_path, scene_id=2)

    assert result.passed
    assert (tmp_path / "scene_2_identity.png").exists()


def test_an_unreachable_model_never_costs_the_video(tmp_path):
    """It reports; it does not block. A run that cost minutes per shot must
    not be lost because a checker was unavailable."""

    class Unreachable(VLMProvider):
        def inspect_image(self, image_path: str, prompt: str) -> str:
            raise ConnectionError("Ollama is not running")

    result = check_identity(
        str(tmp_path / "missing.png"), Unreachable(), species="cat", work_dir=tmp_path, scene_id=1
    )

    assert isinstance(result, IdentityCheck)
    assert not result.passed
    assert any("人工確認" in issue for issue in result.issues)


def test_an_answer_that_is_not_json_is_reported_rather_than_raised(tmp_path):
    vlm = FakeVLM("I'm sorry, I can't help with that.")

    result = check_identity(
        str(tmp_path / "missing.png"), vlm, species="cat", work_dir=tmp_path, scene_id=1
    )

    assert not result.passed


def test_the_prompt_asks_for_decisions_rather_than_offering_a_list_to_copy():
    """An earlier version had a free-text "problems" field whose example
    enumerated the faults to watch for, and got them read back verbatim on
    every image, good ones included — the model was completing the sentence
    rather than inspecting the picture. Naming a fault while *defining* a
    yes/no field is fine and unavoidable; handing over a list of phrases to
    return is not."""
    from pipeline.identity import _PROMPT

    assert "problems" not in _PROMPT
    for decision in ("animals_visible", "body_intact", "sits_in_the_scene"):
        assert decision in _PROMPT


def test_the_prompt_says_a_toy_is_not_an_animal():
    """Measured on a real asset: a shelf of plush toys behind the cat was
    counted as a second animal, which would have sent a reviewer to inspect
    a perfectly good shot. Defining the term is not the same as handing over
    an answer."""
    from pipeline.identity import _PROMPT

    assert "Soft toys" in _PROMPT


def test_an_unknown_vlm_provider_says_what_is_available():
    with pytest.raises(ValueError, match="ollama"):
        get_vlm_provider("gpt-4v")


def test_the_check_can_be_turned_off_for_a_machine_without_the_model():
    """It reports rather than blocks, so switching it off costs a warning,
    not a video — which is the only reason an off switch is acceptable."""
    assert isinstance(config.IDENTITY_CHECK_ENABLED, bool)

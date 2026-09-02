"""Checking that a generated shot still shows this pet, and only this pet.

docs/architecture.md §11 weights Identity Consistency at 30% — the heaviest
item in the QA score — and until now nothing checked it at all. A shot whose
picture was generated can fail in three ways that a person spots instantly
and no other check here catches:

    the animal is gone       the segmenter found nothing and the sampler
                             repainted the frame, so an adoption video shows
                             an empty park
    there is another animal  the generated area grew a second cat, and the
                             video now shows an animal that does not exist
    it is not this animal    a different species, or one warped past
                             recognition by image-to-video

The question is answered against the Pet Profile rather than against the
original photo. That is both what the project says is true (the Profile is
the single source of truth) and what the model can actually do — see
providers/vlm/ollama_vlm_provider.py for what happened when it was asked to
compare two images.

This reports; it does not block. The judgement comes from a 7B model, and
throwing away a run that costs minutes per shot on its opinion would be
worse than showing a reviewer what it noticed.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from pipeline.editing import PHOTO_EXTENSIONS
from providers.base import VLMProvider

# Every field is something the model has to decide by looking. An earlier
# version listed the faults to watch for inside the schema ("distorted
# anatomy, badly blended edges, ...") and got them read back verbatim on
# every image, including good ones — the model was completing the sentence,
# not inspecting the picture. Asking for a count and two plain yes/no
# judgements gives answers that actually differ between shots.
_PROMPT = """\
Look at this image, one shot from an animal shelter's adoption video.

Answer ONLY with a JSON object, no other text and no code fences:
{"animals_visible": <how many real live animals are in the picture, integer. \nSoft toys, figurines, printed pictures and patterns on fabric are not \nanimals, however lifelike>,
 "species": "<species of the main animal: cat, dog, or none>",
 "description": "<one short sentence describing the main animal>",
 "body_intact": <true if the animal's body looks complete and normally \
shaped, false if any part is missing, duplicated or distorted>,
 "sits_in_the_scene": <true if the animal is really standing, sitting or \
lying on something in the picture, false if it seems to hang in mid-air or \
be pasted on top>,
 "upright_in_the_scene": <true if the animal is the right way up for this \
room, false if it looks tipped over, rotated or falling>}
"""


class IdentityCheck(BaseModel):
    """What the vision model saw, and whether it matches the Profile."""

    #: False when the shot cannot be published as-is without a human looking
    #: at it first. Never used to abort a run — see the module docstring.
    passed: bool
    animals_visible: int | None = None
    species: str | None = None
    description: str = ""
    #: Human-readable reasons this shot needs attention, in the reviewer's
    #: language. Empty when passed.
    issues: list[str] = []

    def summary(self) -> str:
        return "；".join(self.issues) if self.issues else "通過"


def _extract_frame(clip_path: str, output_path: str) -> str:
    """Grab a frame from the middle of a clip.

    The middle rather than the first: an Image-to-Video clip starts from the
    source photo and drifts, so the opening frame is the one place the
    animal is guaranteed to still look right.
    """
    duration = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            clip_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            str(float(duration) / 2),
            "-i",
            clip_path,
            "-frames:v",
            "1",
            output_path,
        ],
        check=True,
    )
    return output_path


def _parse(raw: str) -> dict:
    """Pull the JSON object out of the model's answer.

    Same leniency as pipeline/script_gen.py's: these models wrap JSON in
    prose or code fences whatever the prompt says.
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"vision model did not answer with JSON: {raw!r}")
    return json.loads(match.group(0), strict=False)


def check_identity(
    visual_path: str,
    provider: VLMProvider,
    *,
    species: str,
    work_dir: Path,
    scene_id: int,
) -> IdentityCheck:
    """Look at what a generated shot actually shows and judge it against the
    Profile's species.

    visual_path may be a still or a clip; a clip is sampled at its midpoint.
    Everything here — reading the clip, reaching the model, making sense of
    its answer — is inside one try: a shot that could not be checked is
    reported as needing a look, never raised. An unavailable vision model
    must not cost a reviewer the video it was checking.
    """
    try:
        if Path(visual_path).suffix.lower() in PHOTO_EXTENSIONS:
            image_path = visual_path
        else:
            image_path = _extract_frame(
                visual_path, str(work_dir / f"scene_{scene_id}_identity.png")
            )
        raw = provider.inspect_image(image_path, _PROMPT)
        answer = _parse(raw)
    except Exception as e:  # noqa: BLE001 - boundary: an external model and its output
        return IdentityCheck(
            passed=False,
            issues=[f"一致性檢查沒能完成（{type(e).__name__}），這顆鏡頭需要人工確認"],
        )

    return _judge(answer, species=species)


def _judge(answer: dict, *, species: str) -> IdentityCheck:
    """Turn the model's description into a verdict.

    Pure, so the rules are testable without a GPU — and they need to be
    written down somewhere, because the two kinds of finding here carry very
    different weight. How many animals are in the picture, and of what
    species, is close to arithmetic and the model gets it right: those are
    stated as facts. Whether a cut-out looks pasted on is a judgement, and it
    is worded as one thing for a person to look at rather than a verdict.
    """
    count = answer.get("animals_visible")
    seen = (answer.get("species") or "").strip().lower()
    issues: list[str] = []

    if isinstance(count, int):
        if count == 0:
            issues.append("畫面裡看不到任何動物 — 這顆鏡頭沒有拍到這隻寵物")
        elif count > 1:
            issues.append(f"畫面裡有 {count} 隻動物 — 生成的區域多長出了不存在的動物")
    else:
        issues.append("看不出畫面裡有幾隻動物，需要人工確認")

    if seen and seen not in {species.strip().lower(), "none"}:
        issues.append(f"畫面裡像是 {seen}，但 Profile 記錄的是 {species}")

    # Only worth asking about the animal's appearance when there is one; with
    # nothing in the picture these come back meaningless and would bury the
    # finding that actually matters.
    if isinstance(count, int) and count >= 1:
        if answer.get("body_intact") is False:
            issues.append("寵物的身體看起來不完整或變形了，請確認這顆鏡頭")
        if answer.get("upright_in_the_scene") is False:
            # Measured: a cat photographed from above, composited into a room
            # drawn at eye level, came back described as "playfully falling
            # over" — and passed, because it was intact and was on the rug.
            # Nothing read the description, so nothing asked the only
            # question that mattered.
            issues.append("寵物的方向跟場景對不起來（看起來像倒著或翻倒），請確認這顆鏡頭")
        if answer.get("sits_in_the_scene") is False:
            issues.append("寵物看起來像浮在半空或貼上去的，請確認這顆鏡頭")

    return IdentityCheck(
        passed=not issues,
        animals_visible=count if isinstance(count, int) else None,
        species=seen or None,
        description=str(answer.get("description") or "").strip(),
        issues=issues,
    )


def get_vlm_provider(name: str) -> VLMProvider:
    from providers.vlm.ollama_vlm_provider import OllamaVLMProvider

    providers = {"ollama": OllamaVLMProvider}
    try:
        return providers[name]()
    except KeyError:
        raise ValueError(
            f"Unknown VLM provider {name!r}, expected one of {list(providers)}"
        ) from None

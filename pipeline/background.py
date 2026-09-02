"""Giving a photo scene a background before it becomes a shot.

The counterpart of pipeline/i2v.py, one stage earlier: i2v turns a still into
motion, this decides what is around the pet in that still. Two treatments,
and the difference matters editorially, not just visually (see
pipeline/config.py's BACKGROUND_* block):

    EXTEND   keeps the photograph's real background and generates only the
             empty margin the 9:16 frame leaves around it. Nothing the
             camera saw is replaced.
    REPLACE  puts the pet in a setting it has never been in. Real animal,
             invented place — which is why a shot made this way has to be
             disclosed as partly AI-generated.

Kept as its own module so pipeline/rendering.py never has to know which
provider is behind it, and so the frame the pipeline delivers at lives in one
place rather than in every call site.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

from pipeline import config
from providers.base import ImageEditingProvider
from providers.image.comfy_background_provider import ComfyBackgroundProvider


class BackgroundMode(str, enum.Enum):
    """What to do with a photo scene's background.

    str-valued so it survives a round-trip through the job row and a JSON
    response without conversion, the same way JobStatus does.
    """

    #: Show the photograph as it is; the empty frame margin is filled by
    #: pipeline/editing.py's SCENE_FIT_MODE (blurred copy, black, or crop).
    #: Only a script can ask for this — it is what "this shot needs no
    #: generated background" looks like in the timeline.
    KEEP = "keep"
    #: Keep the real background, generate only the empty margin.
    EXTEND = "extend"
    #: Cut the pet out and generate an entirely new setting.
    REPLACE = "replace"

    @property
    def label(self) -> str:
        """What to call this treatment in progress messages the reviewer
        reads. The enum values are wire format; a progress line saying
        "generating replace background" in the middle of Chinese text is
        not something anyone should have to parse."""
        return {
            BackgroundMode.KEEP: "原始",
            BackgroundMode.EXTEND: "延伸",
            BackgroundMode.REPLACE: "置換",
        }[self]


class SceneBackground(BaseModel):
    """The background treatment one shot is getting, and what to generate.

    Produced by resolve_scene_background below rather than read straight off
    the script, because two sources can decide it and they have to be
    reconciled in one place.
    """

    mode: BackgroundMode
    #: None means "the provider's default wording" — a caller who asked for a
    #: treatment without describing it still gets one.
    prompt: str | None = None


def _combine(prompt: str | None, art_direction: str | None) -> str | None:
    """Put the shot's own description and the film's look into one prompt.

    art_direction is what keeps six shots looking like one video instead of
    six: it names the light, the palette, the depth of field, and it has to
    reach every generated frame. Appended rather than prepended so the shot's
    own subject leads — same reasoning as the Wan provider's camera
    constraint.
    """
    parts = [part.strip() for part in (prompt, art_direction) if part and part.strip()]
    return ", ".join(parts) or None


def resolve_scene_background(
    scene: dict,
    *,
    art_direction: str | None = None,
    override_scenes: set[int] | None = None,
    override_mode: BackgroundMode = BackgroundMode.EXTEND,
    override_prompt: str | None = None,
) -> SceneBackground | None:
    """What background treatment this shot gets, or None for none at all.

    Two sources, and the order between them is the point. A reviewer who
    names scenes on the command line is correcting a specific shot, so that
    wins. Otherwise the script's own `background` block decides — which is
    how a video gets a setting that *changes* across the shots (a cage, then
    a street, then a home) instead of the same prompt six times.

    A script that carries no background block at all, or one asking for
    KEEP, means the photograph is shown as photographed.
    """
    scene_id = scene.get("scene_id")
    if override_scenes and scene_id in override_scenes:
        return SceneBackground(mode=override_mode, prompt=override_prompt)

    block = scene.get("background")
    if not isinstance(block, dict):
        return None

    try:
        mode = BackgroundMode(block.get("mode"))
    except ValueError:
        # An unusable mode is not worth failing a render over: the shot is
        # simply shown as photographed, and pipeline/qa.py reports it so a
        # reviewer sees the script asked for something that doesn't exist.
        return None

    if mode is BackgroundMode.KEEP:
        return None

    if mode is BackgroundMode.REPLACE and not config.BACKGROUND_ALLOW_SCRIPT_REPLACE:
        # Downgraded rather than refused: the shot the script wanted is still
        # made, with its real background kept and its margins filled. See
        # config.BACKGROUND_ALLOW_SCRIPT_REPLACE for why a model that cannot
        # see the photograph is not allowed to decide this. pipeline/qa.py
        # reports the downgrade so it is visible rather than silent.
        mode = BackgroundMode.EXTEND

    return SceneBackground(mode=mode, prompt=_combine(block.get("prompt"), art_direction))


_PROVIDERS = {
    "comfy": ComfyBackgroundProvider,
}


def get_image_provider(name: str) -> ImageEditingProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown image provider {name!r}, expected one of {list(_PROVIDERS)}"
        ) from None


def apply_background(
    image_path: str,
    provider: ImageEditingProvider,
    *,
    mode: BackgroundMode,
    output_path: str,
    prompt: str | None = None,
    subject: str | None = None,
) -> str:
    """Give a photo its background treatment, returning the path to use in
    its place.

    For EXTEND that may be image_path itself: a photo already shaped like the
    frame has no margin to generate, and the provider says so by handing the
    original back rather than spending a sampling pass on a copy. Callers
    should use the returned path and not assume a new file exists.

    subject ("cat", "dog") says what REPLACE has to keep. EXTEND never needs
    it — it keeps the whole photograph — so it is only forwarded there.
    """
    if mode is BackgroundMode.REPLACE:
        return provider.replace_background(
            image_path,
            target_width=config.BACKGROUND_WIDTH,
            target_height=config.BACKGROUND_HEIGHT,
            prompt=prompt,
            output_path=output_path,
            subject=subject,
        )
    return provider.outpaint_to_frame(
        image_path,
        target_width=config.BACKGROUND_WIDTH,
        target_height=config.BACKGROUND_HEIGHT,
        prompt=prompt,
        output_path=output_path,
    )

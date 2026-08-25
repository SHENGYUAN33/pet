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

from pipeline import config
from providers.base import ImageEditingProvider
from providers.image.comfy_background_provider import ComfyBackgroundProvider


class BackgroundMode(str, enum.Enum):
    """What to do with a photo scene's background.

    str-valued so it survives a round-trip through the job row and a JSON
    response without conversion, the same way JobStatus does.
    """

    #: Keep the real background, generate only the empty margin.
    EXTEND = "extend"
    #: Cut the pet out and generate an entirely new setting.
    REPLACE = "replace"


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
) -> str:
    """Give a photo its background treatment, returning the path to use in
    its place.

    For EXTEND that may be image_path itself: a photo already shaped like the
    frame has no margin to generate, and the provider says so by handing the
    original back rather than spending a sampling pass on a copy. Callers
    should use the returned path and not assume a new file exists.
    """
    call = (
        provider.replace_background
        if mode is BackgroundMode.REPLACE
        else provider.outpaint_to_frame
    )
    return call(
        image_path,
        target_width=config.BACKGROUND_WIDTH,
        target_height=config.BACKGROUND_HEIGHT,
        prompt=prompt,
        output_path=output_path,
    )

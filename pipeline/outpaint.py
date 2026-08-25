"""Growing a photo into the delivery frame with generated surroundings.

The counterpart of pipeline/i2v.py, one stage earlier: i2v turns a still into
motion, this turns a still that doesn't fill the 9:16 frame into one that
does — by generating what is around the pet rather than by blurring or
cropping (see pipeline/config.py's OUTPAINT_* block for why that matters).

Kept as its own module so pipeline/rendering.py never has to know which
provider is behind it, and so the frame size the pipeline delivers at lives
in one place rather than in every call site.
"""

from __future__ import annotations

from pipeline import config
from providers.base import ImageEditingProvider
from providers.image.comfy_outpaint_provider import ComfyOutpaintProvider

_PROVIDERS = {
    "comfy": ComfyOutpaintProvider,
}


def get_image_provider(name: str) -> ImageEditingProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown image provider {name!r}, expected one of {list(_PROVIDERS)}"
        ) from None


def extend_background(
    image_path: str,
    provider: ImageEditingProvider,
    *,
    output_path: str,
    prompt: str | None = None,
) -> str:
    """Extend a photo to the output frame's shape, returning the path to use
    in its place.

    That may be image_path itself: a photo already shaped like the frame has
    no margin to generate, and the provider says so by handing the original
    back rather than spending a sampling pass on a copy. Callers should use
    the returned path and not assume a new file exists.
    """
    return provider.outpaint_to_frame(
        image_path,
        target_width=config.OUTPAINT_WIDTH,
        target_height=config.OUTPAINT_HEIGHT,
        prompt=prompt,
        output_path=output_path,
    )

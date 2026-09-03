"""Where the pet is in a photo, cached, and never at the cost of a render.

pipeline/layout.py needs to know what part of the frame the animal occupies so
a caption can be put somewhere else. That answer comes from the same
segmentation the background and prop treatments already use, but the two
callers want very different things from it:

    those two    are *generating pixels* and cannot proceed without a mask,
                 so a failure there is a failed shot;
    this one     is only choosing a position, and a failure means falling
                 back to the layout the templates had before — which is a
                 worse-looking shot, not a lost one.

So everything here degrades to None rather than raising. A stopped ComfyUI,
a photo the segmenter finds nothing in, a provider that cannot segment at
all: all of them mean "place the panel where the design would have put it
anyway".

Cached by content, because the same photo is segmented again on every resume,
every single-shot regeneration and every scene that reuses the asset — and
the answer cannot have changed. The cache lives beside the drawn stickers in
storage/decor/, which is already ignored by git and already understood as
regenerable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline import config
from pipeline.layout import Occupancy
from providers.base import ImageEditingProvider


def _cache_key(image_path: Path, subject: str) -> str:
    """Content hash, not path plus mtime.

    The same photo can arrive under two names (uploaded twice, copied between
    pets), and a path-based key would segment it twice. Hashing a few
    megabytes costs milliseconds next to the seconds the segmenter takes.
    """
    digest = hashlib.sha256()
    digest.update(subject.encode())
    digest.update(image_path.read_bytes())
    return digest.hexdigest()[:16]


def mask_path_for(
    image_path: str | Path,
    provider: ImageEditingProvider | None,
    *,
    subject: str | None = None,
) -> Path | None:
    """The subject mask for this photo, from cache or freshly segmented.

    None whenever it cannot be had. Callers pass the result straight to
    Occupancy.from_mask, which also accepts nothing, so neither of them has a
    failure path to write.
    """
    if not config.LAYOUT_AVOID_SUBJECT:
        return None

    image_path = Path(image_path)
    subject = subject or config.BACKGROUND_SUBJECT_FALLBACK
    try:
        key = _cache_key(image_path, subject)
    except OSError:
        # Boundary: the photo itself is unreadable. Whatever is wrong will be
        # reported by the render that is about to try to use it; layout is not
        # the right place to raise it first.
        return None

    cached = config.DECOR_DIR / "masks" / f"{key}.png"
    if cached.exists():
        return cached
    if provider is None:
        return None

    cached.parent.mkdir(parents=True, exist_ok=True)
    try:
        provider.subject_mask(str(image_path), output_path=str(cached), subject=subject)
    except Exception:  # noqa: BLE001 - boundary: an external service, and an optional one
        # Deliberately broad and deliberately silent about the type: every
        # failure here has the same consequence, which is that the panel goes
        # where the design would have put it.
        return None
    return cached if cached.exists() else None


def occupancy_for(
    image_path: str | Path,
    provider: ImageEditingProvider | None,
    *,
    subject: str | None = None,
) -> Occupancy:
    """Where the pet is, as the grid pipeline/layout.py scores against.

    Always returns an Occupancy — an empty one when the mask could not be
    produced — so callers place elements the same way whether or not the
    segmenter was available.
    """
    mask = mask_path_for(image_path, provider, subject=subject)
    if mask is None:
        return Occupancy.empty()
    return Occupancy.from_mask(mask) or Occupancy.empty()

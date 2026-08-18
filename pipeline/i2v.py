from __future__ import annotations

from providers.base import VideoGenerationProvider
from providers.video.cogvideox_provider import CogVideoXProvider
from providers.video.svd_provider import SVDProvider
from providers.video.wan_provider import WanProvider

_PROVIDERS = {
    "svd": SVDProvider,
    "cogvideox": CogVideoXProvider,
    "wan": WanProvider,
}


def get_video_provider(name: str) -> VideoGenerationProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown video provider {name!r}, expected one of {list(_PROVIDERS)}"
        ) from None


def animate_photo(
    image_path: str,
    provider: VideoGenerationProvider,
    *,
    duration: float,
    output_path: str,
    prompt: str | None = None,
) -> str:
    """Animate a still photo (docs/architecture.md §5 strategy B) — only a
    thin pass-through to the provider today, kept as its own function so
    pipeline/rendering.py doesn't need to know provider details. prompt is
    optional motion guidance for text-conditioned providers (CogVideoX, Wan);
    providers that ignore it (SVD) are unaffected."""
    return provider.animate_image(
        image_path, duration_seconds=duration, output_path=output_path, prompt=prompt
    )

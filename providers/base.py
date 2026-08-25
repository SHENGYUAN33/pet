"""Runtime Provider Adapter interfaces.

Concrete design rationale lives in docs/reference/provider_adapter.py and
docs/architecture.md §7. This module is the actual code callers import —
never call a vendor SDK directly from pipeline/ code, always go through
one of these interfaces so a provider can be swapped (commercial <->
open-source) without touching call sites.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return raw text completion for the given prompt."""


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, *, voice_profile: str, language: str, output_path: str) -> str:
        """Synthesize narration audio to output_path, return the path."""


class VideoGenerationProvider(ABC):
    """Strategy B (docs/architecture.md §5): animate a still photo when a
    scene lacks real footage. Output is a short raw clip at the model's
    native fps/duration — pipeline/i2v.py hands it to the same loop/crop/
    constant-fps normalization real video clips already go through, so this
    interface only needs to produce *a* clip, not one of the exact target
    duration."""

    def preflight(self) -> None:
        """Raise if this provider clearly cannot run right now.

        Callers use it to fail before the expensive work leading up to the
        first animate_image call. Only providers with a checkable external
        dependency need to override it (Wan talks to a ComfyUI server that
        has to be started by hand); the default is a no-op, so a provider
        that can't cheaply tell in advance simply reports at call time.

        This is a fast check, not a guarantee: the service can still go away
        between here and the call, which is why animate_image keeps its own
        error handling."""

    @abstractmethod
    def animate_image(
        self,
        image_path: str,
        *,
        duration_seconds: float,
        output_path: str,
        prompt: str | None = None,
    ) -> str:
        """Animate a still image into a short video clip at output_path,
        return the path. prompt is optional per-call motion guidance (e.g.
        "貓輕輕搖尾巴、抬頭看鏡頭") — providers that aren't text-conditioned
        (SVD) ignore it; prompt-aware providers (CogVideoX, Wan) use it to
        steer subject motion instead of falling back to a fixed default."""


class ImageEditingProvider(ABC):
    """Generative editing of a still image, ahead of the video stages.

    Today that means one operation: growing a photo out to the delivery
    frame's aspect ratio with generated surroundings, instead of filling the
    leftover space with blur or black (pipeline/editing.py's SCENE_FIT_MODE).
    The photo's own pixels are carried through untouched — the pet is never
    regenerated — so this stays compatible with the identity-consistency
    requirement that rules out redrawing the subject.

    Output is an ordinary image file. The result goes on to the same Ken
    Burns / Image-to-Video path any other photo takes, so this interface
    needs to produce *an image*, not a finished shot.
    """

    def preflight(self) -> None:
        """Raise if this provider clearly cannot run right now.

        Same contract as VideoGenerationProvider.preflight above: a cheap
        early check so callers fail before the expensive work leading up to
        the first call, with a no-op default for providers that can't
        usefully tell in advance.
        """

    @abstractmethod
    def outpaint_to_frame(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        """Extend image_path to target_width x target_height by generating
        the margin around it, and write the result to output_path (returned).

        The source picture must appear whole and unaltered inside the result
        — this fills space, it does not reframe or repaint. prompt describes
        the surroundings to generate ("溫暖的客廳，午後陽光"); providers
        substitute their own default when it is None.

        A source already at the target aspect ratio has no margin to fill;
        implementations return it unchanged rather than spending a
        generation pass to produce a copy.
        """

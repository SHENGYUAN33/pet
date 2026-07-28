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

    @abstractmethod
    def animate_image(self, image_path: str, *, duration_seconds: float, output_path: str) -> str:
        """Animate a still image into a short video clip at output_path,
        return the path."""

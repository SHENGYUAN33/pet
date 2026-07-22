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
    def synthesize(
        self, text: str, *, voice_profile: str, language: str, output_path: str
    ) -> str:
        """Synthesize narration audio to output_path, return the path."""

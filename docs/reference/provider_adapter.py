"""Provider Adapter interface reference (design spec, not runtime code).

Every AI capability in the system is called through one of these abstract
interfaces. Business logic never imports a vendor SDK directly. A concrete
provider (commercial or open-source) implements the interface; a Router
picks which implementation to call per-request based on sensitivity, cost
cap, latency requirement, and fallback rules (see docs/architecture.md §7).
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Profile summarization, script generation, storyboard breakdown, QA judgment.

    Commercial: Claude / GPT-4o. Open-source: Llama 3.x, Qwen2.5, Mistral (via vLLM).
    """

    @abstractmethod
    def complete(self, prompt: str, *, response_schema: dict | None = None) -> str:
        ...


class VLMProvider(ABC):
    """Photo/video content, expression, pose, scene, and quality analysis.

    Commercial: GPT-4V / Claude Vision. Open-source: Qwen2-VL, LLaVA-NeXT, CogVLM.
    """

    @abstractmethod
    def analyze_media(self, media_url: str, *, instruction: str) -> dict:
        ...


class VideoGenerationProvider(ABC):
    """Per-shot image-to-video / text-to-video generation.

    Commercial: Runway, Google Veo, Kling, Luma.
    Open-source: Stable Video Diffusion, CogVideoX, Open-Sora, Mochi 1.
    """

    @abstractmethod
    def create_image_to_video(
        self,
        image_url: str,
        prompt: str,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> str:
        """Returns a job id; generation is asynchronous — poll or webhook for status."""
        ...


class TTSProvider(ABC):
    """Narration synthesis.

    Commercial: ElevenLabs, Azure/Google TTS.
    Open-source: Coqui XTTS-v2, F5-TTS, GPT-SoVITS, Piper.
    """

    @abstractmethod
    def synthesize(self, text: str, voice_profile: str, language: str) -> str:
        ...


class MusicProvider(ABC):
    """Background music / sound effects generation or licensed library lookup.

    Commercial: Suno/Udio API. Open-source: MusicGen.
    """

    @abstractmethod
    def generate_track(self, mood: str, duration_seconds: int) -> str:
        ...


class ModerationProvider(ABC):
    """Content safety / compliance screening.

    Commercial: OpenAI Moderation API. Open-source: LlamaGuard 3, Detoxify.
    """

    @abstractmethod
    def check(self, content: str | bytes) -> dict:
        """Returns {"flagged": bool, "categories": [...], "reason": str}."""
        ...


# --- Router sketch -----------------------------------------------------
#
# def resolve_provider(capability: str, request_context: dict) -> object:
#     if request_context.get("contains_real_pet_face_or_pii"):
#         return OPEN_SOURCE_PROVIDERS[capability]
#     if request_context.get("cost_used") > request_context.get("cost_cap"):
#         return OPEN_SOURCE_PROVIDERS[capability]
#     if request_context.get("is_urgent"):
#         return COMMERCIAL_PROVIDERS[capability]
#     return OPEN_SOURCE_PROVIDERS[capability]  # default: batch jobs prefer open-source
#
# Concrete providers register themselves under providers/<capability>/<name>.py
# implementing the interface above; routing rules live in a config file, not
# in the calling code, so switching/adding a provider never touches callers.

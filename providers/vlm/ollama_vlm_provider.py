"""Vision-language inspection via the same local Ollama server as the LLM.

One image per call, deliberately. Asking this model to compare a reference
photo against a generated frame in a single two-image call was tried first
and is a trap: told "the first image is the reference, the second is the
frame", it answered about the reference and reported a healthy cat for a
frame containing no animal at all. It can be made to work by ordering it to
ignore the first image, which is a strong hint that whatever it is doing
with two images is not comparison. Shown one image it is accurate — it
counted 1, 3 and 0 animals correctly across the three cases, unprompted
noticing that one cat appeared to float above the grass.

So the identity check asks about one picture and compares the answer with
the Pet Profile, which is the project's single source of truth anyway
(CLAUDE.md: Pet Profile 是唯一事實來源) rather than with another image.
"""

from __future__ import annotations

import base64

import requests

from pipeline import config
from providers.base import VLMProvider


class OllamaVLMProvider(VLMProvider):
    """Open-source VLM via a local Ollama server (e.g. gemma3:12b)."""

    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or config.OLLAMA_VLM_MODEL
        self.host = host or config.OLLAMA_HOST

    def inspect_image(self, image_path: str, prompt: str) -> str:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()

        response = requests.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "images": [encoded],
                "stream": False,
            },
            timeout=config.VLM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["response"]

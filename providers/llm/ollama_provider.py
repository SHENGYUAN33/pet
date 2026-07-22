import requests

from pipeline import config
from providers.base import LLMProvider


class OllamaLLMProvider(LLMProvider):
    """Open-source LLM via a local Ollama server (e.g. qwen2.5:7b-instruct)."""

    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or config.OLLAMA_MODEL
        self.host = host or config.OLLAMA_HOST

    def complete(self, prompt: str) -> str:
        response = requests.post(
            f"{self.host}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()["response"]

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
PROFILES_DIR = STORAGE_DIR / "profiles"
ASSETS_DIR = STORAGE_DIR / "assets"
OUTPUT_DIR = STORAGE_DIR / "output"

# LLM (script generation) — open-source, self-hosted via Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# TTS (narration) — open-source, self-hosted via Coqui XTTS-v2
XTTS_MODEL_NAME = os.getenv(
    "XTTS_MODEL_NAME", "tts_models/multilingual/multi-dataset/xtts_v2"
)
TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "zh-cn")  # XTTS language code for Chinese

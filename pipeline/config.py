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
XTTS_MODEL_NAME = os.getenv("XTTS_MODEL_NAME", "tts_models/multilingual/multi-dataset/xtts_v2")
TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "zh-cn")  # XTTS language code for Chinese

# Subtitle burn-in font (FFmpeg drawtext). Explicit fontfile avoids relying on
# fontconfig, which is frequently unconfigured on Windows and crashes drawtext
# rather than failing gracefully. Default is Traditional Chinese (JhengHei).
DRAWTEXT_FONT_FILE = os.getenv("DRAWTEXT_FONT_FILE", r"C:\Windows\Fonts\msjh.ttc")

# Shot-level generation targets (docs/architecture.md §2): each video is 5-7
# shots of 3-6s each, not a single continuous take. Centralized here per
# CLAUDE.md's "不寫死 magic number" rule instead of scattered across
# script_gen.py's prompt and pipeline/qa.py's structural validator.
MIN_SCENES = int(os.getenv("MIN_SCENES", "5"))
MAX_SCENES = int(os.getenv("MAX_SCENES", "7"))
MIN_SCENE_DURATION = int(os.getenv("MIN_SCENE_DURATION", "3"))
MAX_SCENE_DURATION = int(os.getenv("MAX_SCENE_DURATION", "6"))

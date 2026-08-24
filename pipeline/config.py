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

# Pet catalog + generation job history (MVP: replaces scanning storage/profiles/
# for the pipeline's own reads; storage/assets and storage/output stay on the
# filesystem — the DB only stores metadata, not media).
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://petvideo:changeme@localhost:5433/petvideo"
)
# Seconds to wait for a connection before giving up. Without an explicit
# value libpq waits indefinitely, which turns "PostgreSQL isn't running"
# into a hang rather than an error: `pytest` with Docker stopped took 21
# minutes to report its skips (measured), because each database-backed test
# module probes the connection during collection and printed nothing while
# it waited. Nothing here should ever need seconds to connect — the database
# is local — so a short timeout only ever converts a stall into a prompt
# failure. libpq applies it per host address, and "localhost" resolves to
# both 127.0.0.1 and ::1, so the real worst case is twice this.
# Floored at 1 because libpq reads 0 as "wait forever" — the exact stall this
# setting exists to prevent, which an operator trying to disable the timeout
# would otherwise reintroduce. (libpq also treats 1 as 2, so 1 is the real
# minimum either way.)
DB_CONNECT_TIMEOUT = max(1, int(os.getenv("DB_CONNECT_TIMEOUT", "2")))

# Image-to-Video (docs/architecture.md §5 strategy B) — open-source, self-hosted.
# CogVideoX's officially released image-to-video checkpoint is the 5B variant;
# there is no 2B I2V checkpoint despite "CogVideoX-2B" being the commonly
# cited name for the (text-to-video) line.
SVD_MODEL_NAME = os.getenv("SVD_MODEL_NAME", "stabilityai/stable-video-diffusion-img2vid-xt")
COGVIDEOX_MODEL_NAME = os.getenv("COGVIDEOX_MODEL_NAME", "THUDM/CogVideoX-5b-I2V")
COGVIDEOX_DEFAULT_PROMPT = os.getenv(
    "COGVIDEOX_DEFAULT_PROMPT", "The subject moves naturally and subtly."
)

# Wan2.2 (Wan-AI, Apache 2.0) — unlike SVD, its text prompt actually steers
# subject motion rather than just camera/background movement, which is why
# it's offered as a third video_provider choice alongside SVD/CogVideoX
# rather than replacing them.
#
# Runs through a locally-hosted ComfyUI server (vendor/comfyui/, gitignored),
# not diffusers and not Wan's own official generate.py script — both of
# those were tried first and ruled out on this machine:
#   - diffusers' WanImageToVideoPipeline needs the MoE I2V-A14B checkpoint
#     (two full 14B experts, ~118GB bf16), which doesn't fit this machine's
#     64GB RAM (confirmed: SIGSEGV loading the 2nd of 6 pipeline components).
#   - The smaller TI2V-5B checkpoint fits, but diffusers' WanPipeline has no
#     image-to-video support for it (confirmed open bug,
#     huggingface/diffusers#13258, "image is not part of the pipeline").
#   - Wan's own official generate.py *does* support TI2V-5B image-to-video,
#     and runs standalone (vendor/wan2.2/, no longer used by this provider)
#     — but only in unquantized bf16, which measured ~500s/sampling step on
#     this 16GB card (~7h for a single scene at the default 50 steps):
#     confirmed too slow to be usable.
#   - ComfyUI + an FP8-quantized TI2V-5B checkpoint (Kijai/WanVideo_comfy_fp8_scaled)
#     measured ~25s/step on the same hardware — about 20x faster — because
#     the FP8 weights plus ComfyUI's own memory management avoid the
#     CPU-roundtrip overhead that generate.py's --offload_model/--t5_cpu
#     flags require to fit the unquantized model in 16GB VRAM.
#
# ComfyUI must already be running (see providers/video/wan_provider.py's
# docstring for the start command) — this provider does not spawn it, the
# same way pipeline/rendering.py doesn't spawn Ollama or PostgreSQL.
WAN_COMFYUI_DIR = BASE_DIR / "vendor" / "comfyui"
WAN_COMFYUI_URL = os.getenv("WAN_COMFYUI_URL", "http://127.0.0.1:8188")
WAN_MODEL_FILE = os.getenv(
    "WAN_MODEL_FILE", r"WanVideo\2_2\TI2V\Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors"
)
WAN_VAE_FILE = os.getenv("WAN_VAE_FILE", r"wanvideo\Wan2_2_VAE_bf16.safetensors")
WAN_T5_FILE = os.getenv("WAN_T5_FILE", "umt5-xxl-enc-fp8_e4m3fn.safetensors")
WAN_SAMPLE_STEPS = int(os.getenv("WAN_SAMPLE_STEPS", "20"))
# Max bounding box the source photo is scaled to fit inside, preserving its
# own aspect ratio (providers/video/wan_provider.py uses ImageResizeKJv2's
# "resize" mode, not "crop" — the actual output dimensions depend on the
# source photo's own aspect ratio and will usually be smaller than this box
# in one dimension). Portrait by default since pet photos typically are and
# the final video output is 9:16 vertical anyway (pipeline/editing.py's
# build_scene_clip normalizes everything to 1080x1920) — a landscape box
# would still shrink a portrait source to fit its width, wasting resolution.
WAN_WIDTH = int(os.getenv("WAN_WIDTH", "704"))
WAN_HEIGHT = int(os.getenv("WAN_HEIGHT", "1280"))
WAN_FPS = int(os.getenv("WAN_FPS", "24"))
WAN_DEFAULT_PROMPT = os.getenv("WAN_DEFAULT_PROMPT", "The subject moves naturally and subtly.")
WAN_NEGATIVE_PROMPT = os.getenv(
    "WAN_NEGATIVE_PROMPT",
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，"
    "畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走",
)

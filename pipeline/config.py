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

# How a source that isn't already 9:16 is put into the output frame.
# "blur" fits the whole picture and fills the rest with a blurred copy of
# itself; "pad" uses flat black; "crop" scales up and cuts away whatever
# doesn't fit — which costs a 4:3 photo 59% of its width, usually including
# part of the pet, and is why it is no longer the default.
SCENE_FIT_MODE = os.getenv("SCENE_FIT_MODE", "blur")
SCENE_FIT_BLUR = int(os.getenv("SCENE_FIT_BLUR", "24"))

# Closing recap of the assets the script had no room for. A 30s video is 5-7
# shots showing one asset each, so a pet with thirteen photos leaves six of
# them out; rather than lengthening the story shots (which are paced to one
# line of narration each), the leftovers get a quick cut at the end. Per-asset
# duration shrinks toward RECAP_MIN_ASSET_DURATION as the leftovers pile up,
# so every one of them still appears instead of the recap growing forever.
RECAP_ASSET_DURATION = float(os.getenv("RECAP_ASSET_DURATION", "0.7"))
RECAP_MIN_ASSET_DURATION = float(os.getenv("RECAP_MIN_ASSET_DURATION", "0.35"))
RECAP_MAX_DURATION = float(os.getenv("RECAP_MAX_DURATION", "6"))

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
# Appended to every prompt, the caller's own included. What people ask for
# ("cinematic motion", "make this come alive") reads to the model as camera
# work, and it answers with a push-in or a drift — the photo's framing swims
# while the pet barely moves. Nobody writing a motion description should have
# to know to defend against that, so the camera constraint is not theirs to
# remember. Set WAN_PROMPT_SUFFIX="" to opt out.
WAN_PROMPT_SUFFIX = os.getenv(
    "WAN_PROMPT_SUFFIX",
    " Static locked-off camera, fixed framing, no camera movement, no zoom, no pan.",
)
# Wan's own published negative prompt, minus its anti-stillness terms
# (静态 / 静止 / 静止不动的画面). Those exist to stop the model returning a
# frozen frame, but here they push it the other way: told that stillness is
# bad, the cheapest motion it can produce is moving the camera, which is
# exactly the shaky push-in this pipeline doesn't want. The quality and
# anatomy terms are kept, and camera-movement and warping terms added.
WAN_NEGATIVE_PROMPT = os.getenv(
    "WAN_NEGATIVE_PROMPT",
    "镜头运动，镜头推近，镜头拉远，镜头晃动，变焦，画面抖动，画面漂移，形变，扭曲，"
    "色调艳丽，过曝，细节模糊不清，字幕，风格，作品，画作，画面，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，"
    "畸形的，毁容的，形态畸形的肢体，手指融合，杂乱的背景，三条腿，背景人很多，倒着走",
)
# Sampler settings. Previously hard-coded in wan_provider.py's graph, which
# put the two knobs that decide how violently the picture moves out of reach
# of a .env tweak. shift especially: it scales how far the sampler travels
# from the source frame, so lowering it is the direct fix for "the whole
# image is swimming" — at the cost of a subtler animation.
WAN_CFG = float(os.getenv("WAN_CFG", "5.0"))
WAN_SHIFT = float(os.getenv("WAN_SHIFT", "5.0"))
# Fixed by default so re-running a scene reproduces it; change it to draw a
# different take of the same photo when a result comes out ugly.
WAN_SEED = int(os.getenv("WAN_SEED", "47"))


# --- 背景處理 / Generated backgrounds (docs/architecture.md §5) ---------------
# Two treatments for one photo scene, both run by the same provider
# (providers/image/comfy_background_provider.py) and picked per run:
#
#   extend   Keep the photo's real background and generate only the empty
#            margin the 9:16 frame leaves around it. Nothing the camera saw
#            is replaced, so this is strategy A with the bars filled in —
#            the honest default, and it needs no matting.
#   replace  Cut the pet out and generate an entirely new setting behind it.
#            The pet is still the photographed animal, but the place is
#            invented, which makes it strategy C: it has to carry the
#            AI-generation disclosure below, and it must never imply a fact
#            about the pet (see BACKGROUND_NEGATIVE_PROMPT).
#
# In both cases the subject's own pixels are composited back over the
# generated canvas at the end, so no treatment ever redraws the animal.
# Runs on the same locally-hosted ComfyUI server as Wan2.2 (see WAN_* above).
BACKGROUND_COMFYUI_URL = os.getenv("BACKGROUND_COMFYUI_URL", WAN_COMFYUI_URL)
# Stable Diffusion XL checkpoint, in vendor/comfyui/models/checkpoints/.
BACKGROUND_MODEL_FILE = os.getenv("BACKGROUND_MODEL_FILE", "sd_xl_base_1.0.safetensors")
# How "replace" decides which pixels are the pet. Only that treatment needs
# it; both options are core ComfyUI nodes rather than another custom pack.
#
#   sam3      Text-prompted segmentation: it is told to find "cat", and finds
#             the cat. Default because the alternative gets this wrong in a
#             way that shows: BiRefNet segments the salient *object*, which
#             for a cat lying on a cat tree is the cat AND the cat tree, so
#             the furniture travels to the new setting with it (measured on a
#             real asset — SAM3 cut the same shelf bars out correctly).
#   birefnet  Salient-object matting, no prompt. Smaller (444MB vs 1.7GB) and
#             needs no word for the species, so it stays available for a pet
#             SAM3's vocabulary would not have a name for.
BACKGROUND_MATTE_BACKEND = os.getenv("BACKGROUND_MATTE_BACKEND", "sam3")
# SAM3 checkpoint, in vendor/comfyui/models/checkpoints/ (Comfy-Org/sam3.1).
BACKGROUND_SAM3_MODEL_FILE = os.getenv(
    "BACKGROUND_SAM3_MODEL_FILE", "sam3.1_multiplex_fp16.safetensors"
)
BACKGROUND_SAM3_THRESHOLD = float(os.getenv("BACKGROUND_SAM3_THRESHOLD", "0.5"))
BACKGROUND_SAM3_REFINE_ITERATIONS = int(os.getenv("BACKGROUND_SAM3_REFINE_ITERATIONS", "2"))
# What SAM3 is told to look for when the caller doesn't say. Callers should
# say: the pipeline knows the pet's species and the provider does not, so
# pipeline/rendering.py passes it through. This is the honest fallback for a
# profile whose species field isn't a word SAM3 can act on.
BACKGROUND_SUBJECT_FALLBACK = os.getenv("BACKGROUND_SUBJECT_FALLBACK", "pet")
# Smallest share of the frame the subject may occupy before "replace" refuses
# the shot. Asked for a cat in a photo that has none, SAM3 correctly returns
# nothing — and the graph then happily regenerates the entire frame, giving
# an adoption video a shot of an empty park with no animal in it (measured:
# a stock photo of a person had been added to a pet's profile). Silence is
# the wrong answer there; the run stops and says which asset it was, which
# also happens to be how a reviewer finds out an asset isn't of this pet.
#
# This separates "found nothing" from "found something", not "good shot"
# from "bad shot" — how well a small subject replaces is a judgement for the
# person looking at the result. Measured on real assets: a photo without the
# animal gives exactly 0.0%, while even a cat far away in a cluttered room
# gives about 4%. Anything in between is a comfortable place to draw the
# line.
BACKGROUND_MIN_SUBJECT_COVERAGE = float(os.getenv("BACKGROUND_MIN_SUBJECT_COVERAGE", "0.005"))
# BiRefNet matting weights, in vendor/comfyui/models/background_removal/
# (Comfy-Org/BiRefNet). Used only when BACKGROUND_MATTE_BACKEND is birefnet.
BACKGROUND_MATTE_MODEL_FILE = os.getenv("BACKGROUND_MATTE_MODEL_FILE", "birefnet.safetensors")
# Generation frame: exactly 9:16 so the result drops into the output frame
# with nothing left to pad, and divisible by 8 for the VAE. Smaller than the
# 1080x1920 delivery size on purpose — SDXL is trained around 1024x1024, and
# pipeline/editing.py scales the result up anyway.
BACKGROUND_WIDTH = int(os.getenv("BACKGROUND_WIDTH", "720"))
BACKGROUND_HEIGHT = int(os.getenv("BACKGROUND_HEIGHT", "1280"))
BACKGROUND_STEPS = int(os.getenv("BACKGROUND_STEPS", "25"))
BACKGROUND_CFG = float(os.getenv("BACKGROUND_CFG", "7.0"))
BACKGROUND_SAMPLER = os.getenv("BACKGROUND_SAMPLER", "euler")
BACKGROUND_SCHEDULER = os.getenv("BACKGROUND_SCHEDULER", "karras")
# Fixed by default so re-running a scene reproduces the same surroundings;
# change it to draw a different take of the same photo.
BACKGROUND_SEED = int(os.getenv("BACKGROUND_SEED", "47"))
# "extend" seam: FEATHER softens the mask edge where the margin meets the
# photo, GROW_MASK lets the sampler repaint a little of the photo's own edge
# so the two don't meet on a hard line. Both cost some of the original edge
# pixels, so they stay small.
BACKGROUND_FEATHER = int(os.getenv("BACKGROUND_FEATHER", "40"))
BACKGROUND_GROW_MASK = int(os.getenv("BACKGROUND_GROW_MASK", "16"))
# "replace" seam, around the cut-out subject. The blur is what stops the pet
# reading as a sticker pasted on a picture; the grow is deliberately 0 —
# expanding the subject mask keeps a halo of the *old* background around the
# animal, which is the most obvious tell that a background was swapped.
# GROW_MASK above still applies to the sampler's side, so it repaints
# slightly under the subject's edge rather than leaving that halo behind.
BACKGROUND_SUBJECT_FEATHER = float(os.getenv("BACKGROUND_SUBJECT_FEATHER", "8"))
# Confidence above which a pixel counts as subject. Matting returns
# confidences rather than a binary mask, and on a hazy photo BiRefNet
# returned the whole animal near 0.5 — composited as-is that half-blends the
# pet into the generated scene and it appears as a ghost, so the matte is
# made solid before the edge is softened again.
BACKGROUND_MATTE_THRESHOLD = float(os.getenv("BACKGROUND_MATTE_THRESHOLD", "0.5"))
BACKGROUND_SUBJECT_GROW = int(os.getenv("BACKGROUND_SUBJECT_GROW", "0"))
# English, and not by preference: SDXL's text encoders are CLIP, trained on
# English captions only — a Chinese prompt is not translated, it is embedded
# as noise, and the model falls back to inventing whatever it likes (measured
# here: a zh-TW "warm living room" prompt produced a night-time cityscape
# above the cat). Until something translates the reviewer's wording on the
# way in, every prompt reaching this provider has to be written in English.
#
# It should describe the *whole* picture, not just the background. The masked
# area is generated from the prompt alone, so a prompt that doesn't mention
# what is already in the photo produces surroundings that belong to a
# different scene.
BACKGROUND_DEFAULT_PROMPT = os.getenv(
    "BACKGROUND_DEFAULT_PROMPT",
    "a pet photographed indoors, warm natural home interior, soft afternoon "
    "light, shallow depth of field, realistic photograph",
)
# The generated area must stay scenery. Left to itself the model reads a
# photo of a pet as "this picture contains a pet" and paints another one into
# the space it is given — a second cat that does not exist, which is a
# factual problem, not just an ugly one (CLAUDE.md: Pet Profile 是唯一事實來源).
# The same goes for people: an invented human implies a home situation nobody
# promised. Text is excluded because subtitles are burned in by the editing
# stage, never generated inside the picture.
BACKGROUND_NEGATIVE_PROMPT = os.getenv(
    "BACKGROUND_NEGATIVE_PROMPT",
    "another animal, second pet, extra cat, extra dog, person, human, hands, face, "
    "text, watermark, signature, logo, frame, border, blurry, distorted, lowres, "
    "duplicate, cropped",
)
# Burned onto any shot whose setting was invented rather than photographed
# (mode "replace"). Required by docs/architecture.md §5 strategy C: a viewer
# must not be able to mistake a generated place for where this animal
# actually is. "extend" never carries it — nothing the camera saw is replaced
# there, and labelling a filled margin would dilute the label where it
# matters.
BACKGROUND_DISCLOSURE_TEXT = os.getenv("BACKGROUND_DISCLOSURE_TEXT", "部分畫面由 AI 創意生成")
# Words that must not appear in a generated setting, checked by
# pipeline/fact_check.py before a script is accepted.
#
# A replaced background is a creative choice, but it still makes a claim: a
# living room with a child in it says this animal is good with children, a
# clinic says something about its health, and neither may be true. Nobody
# promised them, and the Pet Profile is the only thing allowed to say what
# is true of this pet. The other animals on the list are there for a second
# reason too — naming one in the prompt makes the model paint one, so the
# video ends up with an animal that does not exist beside the real one.
#
# English because the prompts are (see BACKGROUND_DEFAULT_PROMPT), matched
# on whole words so "human" doesn't fire on "humid".
BACKGROUND_FORBIDDEN_TERMS = tuple(
    term.strip()
    for term in os.getenv(
        "BACKGROUND_FORBIDDEN_TERMS",
        "person,people,human,child,children,kid,kids,boy,girl,man,woman,family,owner,hands,"
        "cat,cats,kitten,dog,dogs,puppy,pet,pets,animal,animals,bird,rabbit,"
        "vet,veterinary,clinic,hospital,medical,surgery",
    ).split(",")
    if term.strip()
)

# --- 一致性檢查 / Identity consistency (docs/architecture.md §11) -------------
# The heaviest item in the QA score (30%), and the only failure mode here that
# a person spots instantly while no other check catches: the pet missing from
# a generated shot, a second animal grown beside it, or an animal warped past
# recognition by image-to-video.
#
# Runs on the shots whose picture was generated, because those are the only
# ones where the animal can change — a Ken Burns shot of a photograph is the
# photograph. Vision model on the same local Ollama server as the script LLM
# (gemma3:12b is multimodal); measured about 4 seconds per shot, next to
# minutes for the generation it is checking.
OLLAMA_VLM_MODEL = os.getenv("OLLAMA_VLM_MODEL", "gemma3:12b")
VLM_TIMEOUT_SECONDS = int(os.getenv("VLM_TIMEOUT_SECONDS", "180"))
# Off switch for a machine without the vision model pulled, or for a batch
# run where the seconds per shot are not wanted. It reports, never blocks, so
# turning it off costs a warning rather than a video.
IDENTITY_CHECK_ENABLED = os.getenv("IDENTITY_CHECK_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}
VLM_PROVIDER = os.getenv("VLM_PROVIDER", "ollama")
# Whether fact-checking may ask the script LLM about wording it cannot match
# by substring. Off falls back to exact matching, which has one failure mode
# — calling a paraphrased disclosure missing — and loses the check for
# invented claims entirely, since no amount of string matching can tell a
# fabricated sentence from a true one.
FACT_CHECK_SEMANTIC_ENABLED = os.getenv("FACT_CHECK_SEMANTIC_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}

# --- 版面裝飾 / Overlay layout (docs/architecture.md §4 字幕/貼圖/特效) --------
# Border, vignette and the pet's details, composited by FFmpeg. No model is
# involved, which after background replacement is the point: the same input
# gives the same output, the animal cannot be altered, and none of it needs
# an AI-generation disclosure.
#
# Which look a video gets is keyed to the script's `style`; what each style
# looks like is a design system and belongs here rather than in something a
# 7B model invents per run.
DECOR_ENABLED = os.getenv("DECOR_ENABLED", "1").lower() not in {"0", "false", "no"}
DECOR_DEFAULT_STYLE = "cute"
DECOR_PALETTES = {
    # 萌系: warm coral, the friendliest of the three.
    "cute": {"accent": "0xFF8FA3"},
    # 溫暖故事: muted amber, quieter than cute and warmer than neutral.
    "warm_story": {"accent": "0xE0A458"},
    # 反差幽默: fresh teal, the one that reads as playful rather than sweet.
    "contrast_humor": {"accent": "0x4FB3A9"},
}
# Inset frame. Drawn inside the picture so every clip stays exactly the
# delivery size and concatenation can keep stream-copying.
DECOR_BORDER_INSET = int(os.getenv("DECOR_BORDER_INSET", "18"))
DECOR_BORDER_WIDTH = int(os.getenv("DECOR_BORDER_WIDTH", "6"))
DECOR_BORDER_OPACITY = float(os.getenv("DECOR_BORDER_OPACITY", "0.9"))
# Corner darkening. PI/5 is a gentle amount — enough to shape the frame,
# little enough that nobody notices it as an effect.
DECOR_VIGNETTE_ANGLE = os.getenv("DECOR_VIGNETTE_ANGLE", "PI/5")
# The pet's name/age/sex/breed, on screen while the viewer is still deciding
# whether to keep watching. Short, because it competes with the hook itself.
DECOR_INFO_CARD_SECONDS = float(os.getenv("DECOR_INFO_CARD_SECONDS", "3.0"))
DECOR_INFO_CARD_FONT_SIZE = int(os.getenv("DECOR_INFO_CARD_FONT_SIZE", "46"))
DECOR_INFO_CARD_Y = int(os.getenv("DECOR_INFO_CARD_Y", "150"))
# Profile stores sex in English; the card is read by adopters in Chinese.
DECOR_SEX_LABELS = {"male": "男生", "female": "女生"}

# Longest subtitle line, measured in half-width units: a CJK character counts
# as two, a latin letter as one. drawtext has no wrapping of its own, so a
# subtitle longer than the frame is simply cut off at both edges — which is
# what happened the moment the script model wrote an English subtitle and
# sailed past the character rule it had been given for narration. The rule
# stays in the prompt, but the picture must not depend on a model obeying it.
SUBTITLE_MAX_UNITS = int(os.getenv("SUBTITLE_MAX_UNITS", "30"))

# Whether the script may choose to replace a setting on its own.
#
# Off, and this is the important line: replacing a background works only as
# well as the pet segments out of that particular photo, and the script model
# never sees the photo. It reads filenames and a Profile. Asked to decide
# where a shot happens, it cannot possibly know that this one is a cat
# stretched flat against pale bedding that the segmenter will only half find
# — which is exactly the run that produced a generated cat with a fragment of
# the real one stuck to its face.
#
# So the script decides between keeping the photograph and filling in its
# margins, both of which leave every camera pixel alone and cannot fail that
# way. Replacing a setting stays available to a person who has looked at the
# photo, per shot, from the review UI.
BACKGROUND_ALLOW_SCRIPT_REPLACE = os.getenv("BACKGROUND_ALLOW_SCRIPT_REPLACE", "0").lower() not in {
    "0",
    "false",
    "no",
}

# How far the pet's details drop when a shot also carries the AI-generation
# disclosure. Both sit at the top of the frame, and stacked without a gap
# they read as one cluttered block — measured on a real shot.
DECOR_DISCLOSURE_CLEARANCE = int(os.getenv("DECOR_DISCLOSURE_CLEARANCE", "70"))

# --- 貼圖 / Stickers (docs/architecture.md §4 字幕/貼圖/特效) -----------------
# Small drawn marks over the picture. The cute half of the editing stage's
# overlay work, and the safe half: a flat mark in a corner does not compete
# with a photograph, whereas an illustrated *background* under a photographic
# animal reads as a cut-out pasted on a drawing.
#
# Drawn by pipeline/stickers.py rather than shipped as artwork — nothing
# binary in version control, tinted to the video's own accent, and the same
# video always gets the same marks. Cached here.
DECOR_STICKERS_ENABLED = os.getenv("DECOR_STICKERS_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}
DECOR_DIR = STORAGE_DIR / "decor"
DECOR_STICKER_SIZE = int(os.getenv("DECOR_STICKER_SIZE", "110"))
DECOR_STICKER_OPACITY = float(os.getenv("DECOR_STICKER_OPACITY", "0.85"))
DECOR_STICKER_MARGIN = int(os.getenv("DECOR_STICKER_MARGIN", "56"))
# The band a mark may sit in. Above it are the pet's details and the
# AI-generation disclosure; below it is the subtitle. Anything outside this
# lands on something a viewer has to read.
DECOR_STICKER_SAFE_TOP = int(os.getenv("DECOR_STICKER_SAFE_TOP", "380"))
DECOR_STICKER_SAFE_BOTTOM = int(os.getenv("DECOR_STICKER_SAFE_BOTTOM", "420"))
# How many marks each narrative style carries, and which. 溫暖故事 gets one
# quiet paw print: a video about an animal that has had a hard time should
# not be covered in sparkles.
DECOR_STICKER_SETS = {
    "cute": ["heart", "sparkle"],
    "warm_story": ["paw"],
    "contrast_humor": ["sparkle", "sparkle"],
    "": [],
}

# --- 版型覆蓋層 / Composed overlays (docs/architecture.md §4) -----------------
# The other half of the editing stage's overlay work, and the half drawtext
# cannot do. A subtitle is one string in one place, so a drawtext filter is
# the right tool; an information panel is a rounded translucent plate, a
# stack of lines each measured against the plate's width, an icon beside
# each, and a speech bubble needs a tail pointing at the animal. Expressing
# that as drawbox+drawtext means computing every coordinate in FFmpeg
# expression syntax against a chain that is already long, with no way to ask
# how wide a string will actually be.
#
# So the composed pieces are laid out in Python with Pillow — which can
# measure text — and handed to FFmpeg as a single transparent PNG. Same
# split as pipeline/stickers.py, one step up in complexity: Pillow draws,
# FFmpeg composites, and the clip is still one encode.
#
# This layer is deterministic and never touches the animal, so like the rest
# of the decoration it needs no AI-generation disclosure.
OVERLAY_ENABLED = os.getenv("OVERLAY_ENABLED", "1").lower() not in {"0", "false", "no"}
# Same typeface as the burned-in subtitle by default: two different fonts on
# one frame reads as two different videos stacked on each other.
OVERLAY_FONT_FILE = os.getenv("OVERLAY_FONT_FILE", DRAWTEXT_FONT_FILE)
# The band a panel may occupy, matching the stickers' safe zone: above it are
# the pet's details and the AI-generation disclosure, below it the subtitle.
# A panel that lands on either covers something a viewer has to read.
OVERLAY_SAFE_TOP = int(os.getenv("OVERLAY_SAFE_TOP", str(DECOR_STICKER_SAFE_TOP)))
OVERLAY_SAFE_BOTTOM = int(os.getenv("OVERLAY_SAFE_BOTTOM", str(DECOR_STICKER_SAFE_BOTTOM)))
OVERLAY_MARGIN = int(os.getenv("OVERLAY_MARGIN", "48"))
# Plate geometry. The plate is white rather than accent-coloured so the text
# on it stays black-on-light at any accent; the accent appears as the rule
# and the outline, which is what ties the panel to the rest of the video.
OVERLAY_PANEL_OPACITY = float(os.getenv("OVERLAY_PANEL_OPACITY", "0.82"))
OVERLAY_PANEL_RADIUS = int(os.getenv("OVERLAY_PANEL_RADIUS", "24"))
OVERLAY_PANEL_PADDING = int(os.getenv("OVERLAY_PANEL_PADDING", "28"))
OVERLAY_OUTLINE_WIDTH = int(os.getenv("OVERLAY_OUTLINE_WIDTH", "3"))
OVERLAY_TEXT_COLOUR = os.getenv("OVERLAY_TEXT_COLOUR", "0x282828")
# Type scale. Three sizes, not a continuum: a headline, the body of a list,
# and the quote in a bubble, which sits between them.
OVERLAY_HEADLINE_SIZE = int(os.getenv("OVERLAY_HEADLINE_SIZE", "58"))
OVERLAY_QUOTE_SIZE = int(os.getenv("OVERLAY_QUOTE_SIZE", "40"))
OVERLAY_BODY_SIZE = int(os.getenv("OVERLAY_BODY_SIZE", "30"))
OVERLAY_LINE_GAP = int(os.getenv("OVERLAY_LINE_GAP", "14"))
# Sidebar width as a fraction of the frame. Wide enough for "疫苗：已完成"
# on one line at the body size, narrow enough to leave the animal visible —
# a panel covering half the picture defeats the point of the picture.
OVERLAY_SIDEBAR_RATIO = float(os.getenv("OVERLAY_SIDEBAR_RATIO", "0.42"))
# The bubble's tail: how far it drops below the plate and how wide its base
# is. Without it the plate is a caption box, not something the pet is saying.
OVERLAY_BUBBLE_TAIL_HEIGHT = int(os.getenv("OVERLAY_BUBBLE_TAIL_HEIGHT", "34"))
OVERLAY_BUBBLE_TAIL_WIDTH = int(os.getenv("OVERLAY_BUBBLE_TAIL_WIDTH", "38"))
# Longest a single overlay string may be before it is truncated, per field.
# Pillow wraps rather than clipping, so the failure mode here is a panel that
# grows until it fills the frame, not text running off the edge.
OVERLAY_MAX_CHARS = int(os.getenv("OVERLAY_MAX_CHARS", "40"))
# Most lines a list-shaped panel will show. A model handed a whole profile
# will happily list twelve facts; four is what fits beside the animal.
OVERLAY_MAX_TAGS = int(os.getenv("OVERLAY_MAX_TAGS", "4"))
# Share of a video's shots that may carry a panel before pipeline/qa.py says
# so. A panel earns its place by being the exception — on every shot it stops
# reading as emphasis and just covers the animal.
OVERLAY_MAX_SHARE = float(os.getenv("OVERLAY_MAX_SHARE", "0.6"))
# Gap between a bottom-anchored panel and the top of the subtitle band. The
# band's floor was set for stickers — small, sparse marks — and a plate
# resting on it reads as one block with the subtitle underneath. The subtitle
# also grows upward as it wraps (it is anchored by the bottom of its text
# block), so the clearance is what a third line eats into instead of the
# panel.
OVERLAY_SUBTITLE_CLEARANCE = int(os.getenv("OVERLAY_SUBTITLE_CLEARANCE", "70"))

# Burned-in subtitle type. Size lives here rather than inline in the filter
# for the same reason every other threshold does — and because the line
# spacing below is only correct relative to it.
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "54"))
# Extra pixels between wrapped subtitle lines, on top of the font's own line
# height. Negative, because the font's own is far too generous.
#
# drawtext's default is 0, so this is not a loose default being tightened —
# it is the face's metrics. Measured on msjh at size 54: consecutive lines
# land 144px apart while the glyphs are 47px tall, leaving a ~100px blank
# between them. Two lines of subtitle read as two unrelated captions rather
# than one sentence that wrapped.
#
# The number was swept rather than derived, because the arithmetic is not the
# obvious one: the block is anchored by the *bottom* of its text (see the
# subtitle's y= in pipeline/editing.py), so shrinking the line height also
# slides the block down, and the gap closes at twice the rate the option
# suggests — measured advance is 144 + 2 x this value, and -60 collided the
# two lines outright. -32 lands at an 80px advance, about 1.5x the type size
# and ordinary CJK leading, with 33px of clear space between glyphs.
#
# Measured for this face at this size: change either and measure again.
SUBTITLE_LINE_SPACING = int(os.getenv("SUBTITLE_LINE_SPACING", "-32"))

# --- 版面字型 / Overlay typefaces ---------------------------------------------
# One face for everything reads as a system dialog, not as something made for
# a shelter's audience. Two roles, chosen by what the panel is doing:
#
#   HANDWRITTEN  the held quote — the pet's own voice, so it should not look
#                typeset. Open-source options that suit it: 辰宇落雁體
#                (ChenYuluoyan), 隨峰體 (SuiFeng).
#   ROUND        the bubble, the sidebar and the contact card — information a
#                reader has to take in quickly, warm rather than clinical.
#                Open-source: 粉圓體 (Huninn), 芫荽體 (Cilantro).
#
# Neither is shipped: fonts are large binaries with their own licences, and
# nothing binary goes into version control (the same rule that has
# pipeline/stickers.py drawing its shapes). Drop the .ttf into
# storage/fonts/ and point these at it.
#
# The defaults are what this machine actually has. 標楷體 (kaiu) is a stand-in
# for the handwritten role, not a match: it is a formal kai face, but it is
# brush-derived and reads far less mechanical than 正黑體 next to it. Anything
# unreadable falls back to DRAWTEXT_FONT_FILE, so a missing file costs a
# typeface and never a video.
OVERLAY_FONT_HANDWRITTEN = os.getenv("OVERLAY_FONT_HANDWRITTEN", r"C:\Windows\Fonts\kaiu.ttf")
OVERLAY_FONT_ROUND = os.getenv("OVERLAY_FONT_ROUND", r"C:\Windows\Fonts\msjh.ttc")

# --- 版面立體感 / Overlay depth ------------------------------------------------
# A flat white rectangle on a photograph reads as a screenshot pasted over it.
# A soft drop shadow is what makes it read as a card lying on the picture —
# and it is the cheapest possible way to get there, since it is the panel's
# own alpha, blurred and darkened.
#
# Kept weak on purpose: a shadow anyone notices as a shadow is too strong.
OVERLAY_SHADOW_BLUR = int(os.getenv("OVERLAY_SHADOW_BLUR", "8"))
OVERLAY_SHADOW_OPACITY = float(os.getenv("OVERLAY_SHADOW_OPACITY", "0.15"))
OVERLAY_SHADOW_OFFSET_Y = int(os.getenv("OVERLAY_SHADOW_OFFSET_Y", "6"))
OVERLAY_SHADOW_OFFSET_X = int(os.getenv("OVERLAY_SHADOW_OFFSET_X", "0"))
# How far a speech bubble tilts, in degrees. Fixed rather than random: the
# same video has to render the same way twice, or a resumed run produces a
# shot that does not match the one it is continuing (the same reason
# BACKGROUND_SEED is pinned). The sign alternates by scene so consecutive
# bubbles do not lean the same way.
OVERLAY_BUBBLE_TILT = float(os.getenv("OVERLAY_BUBBLE_TILT", "3.0"))

# --- 版面圖示 / Overlay icons --------------------------------------------------
# A small mark in front of each sidebar line, so the panel scans as a list of
# facts rather than a paragraph. Drawn with Pillow for the same three reasons
# the stickers are (nothing binary in version control, tinted to the video's
# own accent, deterministic) — see pipeline/stickers.py.
#
# Which mark a line gets is keyed off words in the line itself, because the
# line is written by the script model and there is no structured field to
# read. Unmatched lines get the neutral one rather than no icon: a list where
# some rows are indented and others are not looks broken.
OVERLAY_ICONS_ENABLED = os.getenv("OVERLAY_ICONS_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}
OVERLAY_ICON_SIZE = int(os.getenv("OVERLAY_ICON_SIZE", "30"))
OVERLAY_ICON_GAP = int(os.getenv("OVERLAY_ICON_GAP", "12"))
# Keyword -> icon shape. Matched as substrings against the line, first hit
# wins, so order matters: "健康檢查" must not be read as "檢查" alone.
OVERLAY_ICON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("年齡", "cake"),
    ("歲", "cake"),
    ("生日", "cake"),
    ("疫苗", "syringe"),
    ("結紮", "syringe"),
    ("晶片", "syringe"),
    ("健康", "syringe"),
    ("驅蟲", "syringe"),
    ("個性", "heart"),
    ("性格", "heart"),
    ("親人", "heart"),
    ("品種", "paw"),
    ("性別", "paw"),
    ("體型", "paw"),
)
OVERLAY_ICON_DEFAULT = os.getenv("OVERLAY_ICON_DEFAULT", "paw")

# --- 局部道具 / Props on the pet (docs/architecture.md §5 strategy C) ---------
# Painting a small object onto the animal itself: a collar around its neck, a
# toy beside its paws.
#
# This is a different kind of edit from anything above it, and the difference
# is not cosmetic. Every background treatment ends by compositing the pet's
# real pixels back (ImageCompositeMasked), so the guarantee is "the animal is
# never repainted". A collar cannot honour that — the band has to sit *on*
# the animal, which means deliberately not pasting part of it back. So:
#
#   * it always carries the AI-generation disclosure, like REPLACE;
#   * a script may never choose it (PROPS_ALLOW_SCRIPT below), because a model
#     that cannot see the photograph cannot judge whether the animal is in a
#     pose where this works;
#   * the region is named by the person looking at the photo, not guessed
#     from a fixed fraction of the bounding box. A first design took "the top
#     15-30% of the subject" as the neck, which assumes an upright animal
#     with its head up; the photos in this project are as often top-down, or
#     of a cat lying on its back, where that band lands on the floor.
#
# What is deliberately NOT here: a human hand reaching into frame. A hand
# touching the animal states that it tolerates being handled by a stranger,
# which the Profile never said — BACKGROUND_FORBIDDEN_TERMS bans exactly
# those words from a generated setting for that reason, and generating one
# here would be a back door around pipeline/fact_check.py rather than a
# feature.
PROPS_ALLOW_SCRIPT = os.getenv("PROPS_ALLOW_SCRIPT", "0").lower() not in {"0", "false", "no"}
# ControlNet holds the animal's own edges while the masked area is repainted.
# It is not what keeps the face intact — the composite does that, and it did
# before this existed. What it buys is a prop that follows the body's real
# shape instead of floating on top of it.
#
# Canny rather than depth or pose: ComfyUI's Canny node is core, so the edge
# map needs no custom node pack, and an outline is the right hint for "keep
# this silhouette" anyway.
PROPS_CONTROLNET_FILE = os.getenv(
    "PROPS_CONTROLNET_FILE", "controlnet-canny-sdxl-1.0-fp16.safetensors"
)
# Weak, and released early (below). Measured: at 0.65 held to 80% of the
# steps, a collar simply did not appear — the edge map has no collar in it,
# so a strong ControlNet is an instruction to keep the neck exactly as
# photographed, which is the opposite of what a prop pass is for. It has to
# guide the silhouette without forbidding new edges inside the mask.
PROPS_CONTROLNET_STRENGTH = float(os.getenv("PROPS_CONTROLNET_STRENGTH", "0.35"))
# Released partway so the later steps are free to form the prop's own edges
# and texture, which are not in the photograph and so are not in the hint.
PROPS_CONTROLNET_END = float(os.getenv("PROPS_CONTROLNET_END", "0.5"))
PROPS_CANNY_LOW = float(os.getenv("PROPS_CANNY_LOW", "0.2"))
PROPS_CANNY_HIGH = float(os.getenv("PROPS_CANNY_HIGH", "0.5"))
PROPS_STEPS = int(os.getenv("PROPS_STEPS", "28"))
PROPS_CFG = float(os.getenv("PROPS_CFG", "6.5"))
PROPS_DENOISE = float(os.getenv("PROPS_DENOISE", "1.0"))
PROPS_SEED = int(os.getenv("PROPS_SEED", "47"))
# The painted region, as a fraction of the frame, centred on the point the
# reviewer named. A collar is a band across the body; a toy is a blob beside
# it, so they are not the same shape.
PROPS_COLLAR_WIDTH = float(os.getenv("PROPS_COLLAR_WIDTH", "0.42"))
PROPS_COLLAR_HEIGHT = float(os.getenv("PROPS_COLLAR_HEIGHT", "0.13"))
PROPS_TOY_WIDTH = float(os.getenv("PROPS_TOY_WIDTH", "0.26"))
PROPS_TOY_HEIGHT = float(os.getenv("PROPS_TOY_HEIGHT", "0.20"))
# Softening on the painted region's edge, so the prop meets the photograph
# gradually rather than on a cut line.
PROPS_MASK_FEATHER = float(os.getenv("PROPS_MASK_FEATHER", "10"))
PROPS_MASK_GROW = int(os.getenv("PROPS_MASK_GROW", "4"))
# English, and for the same reason every other prompt here is: SDXL's text
# encoders are CLIP, trained on English captions only (see
# BACKGROUND_DEFAULT_PROMPT). One default per placement, because the two are
# describing different things.
PROPS_COLLAR_PROMPT = os.getenv(
    "PROPS_COLLAR_PROMPT",
    # No "pet collar": the word is on PROPS_FORBIDDEN_TERMS, and a default
    # that its own rule would reject is a rule nobody can trust. Dropping it
    # costs nothing — the collar is the object being described either way.
    "photorealistic, a cute soft knitted collar around the neck, small "
    "fabric texture, natural indoor lighting, sharp focus, highly detailed",
)
PROPS_TOY_PROMPT = os.getenv(
    "PROPS_TOY_PROMPT",
    "photorealistic, a small plush toy resting on the floor, soft fabric "
    "texture, natural indoor lighting, gentle shadow, sharp focus, highly detailed",
)
# The animal is already in the picture; the sampler must add an object, not a
# second creature or a person. The anatomy terms are here because a mask that
# clips a paw invites the model to redraw it badly, and a deformed paw on a
# real adoptable animal is worse than no prop at all.
PROPS_NEGATIVE_PROMPT = os.getenv(
    "PROPS_NEGATIVE_PROMPT",
    "another animal, second pet, extra cat, extra dog, person, human, hand, hands, "
    "fingers, arm, face, distorted paws, bad anatomy, deformed, extra limbs, "
    "text, watermark, signature, logo, blurry, lowres",
)
# Burned onto any shot wearing a generated prop, for a stronger reason than a
# replaced setting carries one: this is painted on the animal itself.
#
# Worded separately from BACKGROUND_DISCLOSURE_TEXT rather than reusing it,
# and the two are joined when both apply. "部分畫面由 AI 創意生成" says the
# surroundings are invented; it does not say the animal is wearing something
# it has never worn, and that is the part a viewer would be most surprised to
# learn afterwards.
PROPS_DISCLOSURE_TEXT = os.getenv("PROPS_DISCLOSURE_TEXT", "含 AI 道具裝飾")
#: Between two labels on one line. A middle dot rather than a comma: this is
#: a list of two notices, not a sentence.
DISCLOSURE_JOINER = os.getenv("DISCLOSURE_JOINER", "・")
# Smallest share of the frame the subject mask may cover before a prop run is
# refused. Same guard, same reasoning as BACKGROUND_MIN_SUBJECT_COVERAGE: if
# SAM3 cannot find the animal, the region the reviewer named is not on any
# animal, and painting there produces a prop lying on an empty floor.
# A fraction of the frame, not a percentage — the same units mask_coverage()
# returns and BACKGROUND_MIN_SUBJECT_COVERAGE uses. Written as 0.5 the first
# time, which meant 50%% and refused a photo where SAM3 had found the cat
# perfectly well at 27%%.
PROPS_MIN_SUBJECT_COVERAGE = float(os.getenv("PROPS_MIN_SUBJECT_COVERAGE", "0.005"))

# Words a prop description may not contain, checked before anything reaches
# ComfyUI (pipeline/props.py forbidden_terms_in).
#
# BACKGROUND_FORBIDDEN_TERMS plus the body parts, because the two prompts are
# describing different things and a prop prompt can go wrong in a way a
# background prompt cannot: it is painted *on the animal*, so "a hand gently
# holding the cat" would put a person in contact with a real adoptable pet.
# That is a claim the Profile never made — the same reason the background list
# exists — and the prop mask makes it more convincing, not less.
#
# The live-animal half is here for the other reason the background list has
# it: naming an animal makes the model paint one, and a toy region that sits
# *beside* the pet is exactly where a second cat would appear.
#
# Checked as whole words, so "cat" does not fire on "delicate" and "arm" does
# not fire on "warm" — that last one matters, since warm lighting is in the
# shipped defaults.
PROPS_FORBIDDEN_TERMS = tuple(
    dict.fromkeys(
        BACKGROUND_FORBIDDEN_TERMS
        + tuple(
            term.strip()
            for term in os.getenv(
                "PROPS_EXTRA_FORBIDDEN_TERMS",
                "hand,finger,fingers,arm,arms,wrist,palm,skin,body,person's,human's,"
                "puppy,puppies,kittens,creature,fur",
            ).split(",")
            if term.strip()
        )
    )
)
# Share of a video's shots that may carry a generated prop before
# pipeline/qa.py says so.
#
# An adoption video exists to show an adopter this animal. Every shot wearing
# something it does not own stops being a record of the pet and becomes a
# costume shoot — and the adopter is deciding, from these frames, what they
# would be taking home. Half is generous; the point is that the plain shots
# have to outnumber the dressed ones.
PROPS_MAX_SCENE_RATIO = float(os.getenv("PROPS_MAX_SCENE_RATIO", "0.5"))

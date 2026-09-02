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

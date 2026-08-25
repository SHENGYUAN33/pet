from __future__ import annotations

from pathlib import Path

from pipeline import config
from providers.base import VideoGenerationProvider
from providers.comfy_client import ComfyUIClient


def _with_camera_constraint(prompt: str) -> str:
    """Add the locked-camera instruction to whatever motion the caller asked
    for.

    Left to itself the model reads a request for motion as a request for
    camera work and answers with a push-in, so the pet barely moves while the
    whole frame drifts. The constraint therefore belongs to every prompt, not
    to whoever remembered to type it — appended rather than prepended so the
    caller's own description still leads."""
    if not config.WAN_PROMPT_SUFFIX or config.WAN_PROMPT_SUFFIX.strip() in prompt:
        return prompt
    return prompt.rstrip() + config.WAN_PROMPT_SUFFIX


class WanProvider(VideoGenerationProvider):
    """Open-source Image-to-Video via Wan2.2 (Wan-AI, Apache 2.0), run
    through a locally-hosted ComfyUI server with an FP8-quantized
    TI2V-5B checkpoint (Kijai/WanVideo_comfy_fp8_scaled).

    Unlike SVD, Wan2.2 is prompt-conditioned — the caller's prompt actually
    steers subject motion (e.g. a pet moving its tail or head), not just
    camera/background movement.

    Why ComfyUI and not diffusers or Wan's own generate.py script: both were
    tried first on this machine (RTX 5070 Ti, 16GB VRAM / 64GB RAM) and ruled
    out — see pipeline/config.py's WAN_* comment block for the full
    reasoning. Short version: the unquantized checkpoint that has a working
    image-to-video code path measured ~500s/sampling step (~7h/scene) here;
    ComfyUI + the FP8-quantized checkpoint measured ~25s/step (~20x faster,
    ~8min/scene including model load) on identical hardware.

    Requires ComfyUI already running — this provider doesn't spawn it, same
    as pipeline/rendering.py not spawning Ollama or PostgreSQL. Start it in
    its own terminal (needs vendor/comfyui/.venv set up first, see
    STARTUP.md):

        cd vendor/comfyui
        .venv/Scripts/activate
        python main.py --listen 127.0.0.1 --port 8188

    The API-format prompt graph below is built by node-parameter *name*,
    not by copying a UI-format workflow's widgets_values array positions —
    that array's ordering doesn't reliably match /object_info's declared
    required+optional order (optional link-only inputs can be interleaved
    with required widgets in the true source order, and ComfyUI silently
    injects an extra "control_after_generate" slot after any seed widget),
    so a positional conversion silently produced corrupted values when this
    was first tried. Building by name sidesteps that class of bug entirely.
    """

    def __init__(self, base_url: str | None = None):
        self.client = ComfyUIClient(base_url or config.WAN_COMFYUI_URL)

    def _build_prompt(
        self, image_filename: str, prompt: str, num_frames: int, output_prefix: str
    ) -> dict:
        return {
            "58": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "71": {
                "class_type": "ImageResizeKJv2",
                "inputs": {
                    "image": ["58", 0],
                    "width": config.WAN_WIDTH,
                    "height": config.WAN_HEIGHT,
                    "upscale_method": "lanczos",
                    # "resize" fits the source image's own aspect ratio inside
                    # the width/height box below (scales by
                    # min(box_w/W, box_h/H), no cropping) rather than forcing
                    # it to exactly WAN_WIDTH x WAN_HEIGHT — "crop" was cutting
                    # away most of a portrait pet photo to force it into a
                    # differently-shaped box, which is why the animated result
                    # looked like an unrecognizable zoomed-in crop rather than
                    # a subtly animated version of the original photo.
                    "keep_proportion": "resize",
                    "pad_color": "0, 0, 0",
                    "crop_position": "center",
                    "divisible_by": 32,
                },
            },
            "22": {
                "class_type": "WanVideoModelLoader",
                "inputs": {
                    "model": config.WAN_MODEL_FILE,
                    "base_precision": "bf16",
                    "quantization": "disabled",
                    "load_device": "offload_device",
                    "attention_mode": "sdpa",
                },
            },
            "38": {
                "class_type": "WanVideoVAELoader",
                "inputs": {"model_name": config.WAN_VAE_FILE, "precision": "bf16"},
            },
            "11": {
                "class_type": "LoadWanVideoT5TextEncoder",
                "inputs": {
                    "model_name": config.WAN_T5_FILE,
                    "precision": "bf16",
                    "load_device": "offload_device",
                    "quantization": "disabled",
                },
            },
            "16": {
                "class_type": "WanVideoTextEncode",
                "inputs": {
                    "positive_prompt": prompt,
                    "negative_prompt": config.WAN_NEGATIVE_PROMPT,
                    "t5": ["11", 0],
                    "force_offload": True,
                    "model_to_offload": ["22", 0],
                    "use_disk_cache": False,
                    "device": "gpu",
                },
            },
            "70": {
                "class_type": "WanVideoEncode",
                "inputs": {
                    "vae": ["38", 0],
                    "image": ["71", 0],
                    "enable_vae_tiling": False,
                    "tile_x": 272,
                    "tile_y": 272,
                    "tile_stride_x": 144,
                    "tile_stride_y": 128,
                    "noise_aug_strength": 0.0,
                    "latent_strength": 1.0,
                },
            },
            "78": {
                "class_type": "WanVideoEmptyEmbeds",
                "inputs": {
                    "width": ["71", 1],
                    "height": ["71", 2],
                    "num_frames": num_frames,
                    "extra_latents": ["70", 0],
                },
            },
            "27": {
                "class_type": "WanVideoSampler",
                "inputs": {
                    "model": ["22", 0],
                    "image_embeds": ["78", 0],
                    "steps": config.WAN_SAMPLE_STEPS,
                    "cfg": config.WAN_CFG,
                    "shift": config.WAN_SHIFT,
                    "seed": config.WAN_SEED,
                    "force_offload": True,
                    "scheduler": "unipc",
                    "riflex_freq_index": 0,
                    "text_embeds": ["16", 0],
                    "denoise_strength": 1.0,
                    "batched_cfg": False,
                    "rope_function": "comfy",
                    "start_step": 0,
                    "end_step": -1,
                    "add_noise_to_samples": False,
                },
            },
            "28": {
                "class_type": "WanVideoDecode",
                "inputs": {
                    "vae": ["38", 0],
                    "samples": ["27", 0],
                    "enable_vae_tiling": False,
                    "tile_x": 272,
                    "tile_y": 272,
                    "tile_stride_x": 144,
                    "tile_stride_y": 128,
                    "normalization": "default",
                },
            },
            "92": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["28", 0],
                    "frame_rate": config.WAN_FPS,
                    "loop_count": 0,
                    "filename_prefix": output_prefix,
                    "format": "video/h264-mp4",
                    "pingpong": False,
                    "save_output": True,
                },
            },
        }

    def preflight(self) -> None:
        """This provider never starts ComfyUI itself (same as rendering not
        starting Ollama or PostgreSQL — see STARTUP.md), so a stopped server
        is the most common way a run fails."""
        self.client.ping()

    def animate_image(
        self,
        image_path: str,
        *,
        duration_seconds: float,
        output_path: str,
        prompt: str | None = None,
    ) -> str:
        # Repeated per call, not just at preflight time: the server can be
        # stopped between scenes, and eight minutes of Wan work per scene
        # makes that a real window.
        self.preflight()

        image_filename = self.client.upload_image(image_path)

        # frame_num must be 4n+1 per Wan2.2's own constraint (see vendor/wan2.2's
        # generate.py --frame_num help text — the ComfyUI node inherits the
        # same requirement from the underlying model).
        raw_frames = round(duration_seconds * config.WAN_FPS)
        num_frames = max(5, ((raw_frames - 1) // 4) * 4 + 1)

        output_prefix = Path(output_path).stem
        api_prompt = self._build_prompt(
            image_filename,
            _with_camera_constraint(prompt or config.WAN_DEFAULT_PROMPT),
            num_frames,
            output_prefix,
        )

        entry = self.client.run(api_prompt)
        return self.client.fetch_output(entry["outputs"]["92"]["gifs"][0], output_path)

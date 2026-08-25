"""Outpainting a pet photo out to the delivery frame, via ComfyUI + SDXL.

What this replaces: a landscape photo in a 9:16 video leaves two empty bands,
and pipeline/editing.py can only fill them with a blurred copy of the photo,
flat black, or a crop that throws away most of the picture. Here the bands
are generated instead — the photo keeps every one of its own pixels and gains
surroundings that continue it.

Only the margin is generated. The mask handed to the sampler covers the
padding and (via grow_mask_by) a thin band of the photo's edge so the seam
isn't a hard line; everything inside that stays the original photograph. The
pet is therefore never redrawn, which is what makes this usable at all under
the identity-consistency rule — see providers/base.py's ImageEditingProvider.

Core ComfyUI nodes only (ImagePadForOutpaint / VAEEncodeForInpaint / KSampler),
so the one thing that has to be installed is a checkpoint in
vendor/comfyui/models/checkpoints/ — no custom node pack.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline import config
from providers.base import ImageEditingProvider
from providers.comfy_client import ComfyUIClient

#: Latent grid the VAE works on. Fitted dimensions are rounded to it so the
#: generated/kept boundary lands on a latent edge rather than a third of the
#: way into one, which is where soft double-edges at the seam come from.
LATENT_ALIGN = 8


class OutpaintMargins:
    """How much to add on each side, and what the photo is scaled to first.

    Plain object rather than a pydantic model: it never crosses a process
    boundary and is never built from user input — it is arithmetic held
    together for one call.
    """

    def __init__(
        self, fit_width: int, fit_height: int, left: int, top: int, right: int, bottom: int
    ):
        self.fit_width = fit_width
        self.fit_height = fit_height
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    @property
    def is_empty(self) -> bool:
        """True when the source already matches the frame's shape, so there
        is no margin to generate and nothing worth a sampling pass."""
        return not any((self.left, self.top, self.right, self.bottom))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"OutpaintMargins(fit={self.fit_width}x{self.fit_height}, "
            f"l={self.left}, t={self.top}, r={self.right}, b={self.bottom})"
        )


def plan_margins(
    source_width: int, source_height: int, target_width: int, target_height: int
) -> OutpaintMargins:
    """Scale the source to fit whole inside the target frame, then work out
    the margin left over on each side.

    Fit-whole, not fill-and-crop: cutting the picture down to the frame's
    shape is precisely the behaviour this feature exists to stop. The fitted
    size is rounded *down* to the latent grid, so rounding turns into a pixel
    or two more generated margin rather than a pixel of the photo being lost.
    """
    scale = min(target_width / source_width, target_height / source_height)
    fit_width = max(LATENT_ALIGN, int(source_width * scale) // LATENT_ALIGN * LATENT_ALIGN)
    fit_height = max(LATENT_ALIGN, int(source_height * scale) // LATENT_ALIGN * LATENT_ALIGN)

    horizontal = target_width - fit_width
    vertical = target_height - fit_height
    left = horizontal // 2
    top = vertical // 2
    # The odd pixel goes to the right/bottom side, so the four margins plus
    # the fitted photo add up to exactly the target frame.
    return OutpaintMargins(
        fit_width=fit_width,
        fit_height=fit_height,
        left=left,
        top=top,
        right=horizontal - left,
        bottom=vertical - top,
    )


def probe_image_size(image_path: str) -> tuple[int, int]:
    """Read an image's pixel dimensions with ffprobe.

    ffprobe rather than Pillow because FFmpeg is already a hard requirement
    of this project while Pillow is only in the optional [i2v] extras — one
    dependency instead of two for reading two integers.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            image_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)


class ComfyOutpaintProvider(ImageEditingProvider):
    """SDXL outpainting through the same locally-hosted ComfyUI server that
    runs Wan2.2 (see pipeline/config.py's WAN_* block for why that server
    exists). It must already be running — this provider does not spawn it
    (STARTUP.md).
    """

    def __init__(self, base_url: str | None = None):
        self.client = ComfyUIClient(base_url or config.OUTPAINT_COMFYUI_URL)

    def _build_prompt(
        self, image_filename: str, margins: OutpaintMargins, prompt: str, output_prefix: str
    ) -> dict:
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "2": {
                "class_type": "ImageScale",
                "inputs": {
                    "image": ["1", 0],
                    "upscale_method": "lanczos",
                    "width": margins.fit_width,
                    "height": margins.fit_height,
                    # "disabled": the aspect ratio was already preserved when
                    # plan_margins computed these two numbers, so cropping
                    # here would be the node second-guessing that.
                    "crop": "disabled",
                },
            },
            "3": {
                "class_type": "ImagePadForOutpaint",
                "inputs": {
                    "image": ["2", 0],
                    "left": margins.left,
                    "top": margins.top,
                    "right": margins.right,
                    "bottom": margins.bottom,
                    "feathering": config.OUTPAINT_FEATHER,
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": config.OUTPAINT_MODEL_FILE},
            },
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": config.OUTPAINT_NEGATIVE_PROMPT, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "VAEEncodeForInpaint",
                "inputs": {
                    "pixels": ["3", 0],
                    "vae": ["4", 2],
                    # ImagePadForOutpaint's second output is the mask marking
                    # exactly the added margin — the photo is never in it.
                    "mask": ["3", 1],
                    "grow_mask_by": config.OUTPAINT_GROW_MASK,
                },
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["4", 0],
                    "seed": config.OUTPAINT_SEED,
                    "steps": config.OUTPAINT_STEPS,
                    "cfg": config.OUTPAINT_CFG,
                    "sampler_name": config.OUTPAINT_SAMPLER,
                    "scheduler": config.OUTPAINT_SCHEDULER,
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    # 1.0 because VAEEncodeForInpaint has already erased the
                    # masked area — there is no original content left there
                    # to preserve, and a lower value only leaves the erase
                    # showing through.
                    "denoise": 1.0,
                },
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
            # The decode returns the *whole* canvas rebuilt from latents, the
            # photo included — a VAE round-trip visibly softens and re-tints
            # it. So the original pixels are put back on top: invert the pad
            # mask to get "everywhere the photo was", and composite the
            # padded source over the generated canvas through it. What
            # survives is the photograph itself, with generated content only
            # where there was nothing before. Without this step the promise
            # that the pet is never redrawn would be false in practice.
            "11": {"class_type": "InvertMask", "inputs": {"mask": ["3", 1]}},
            "12": {
                "class_type": "ImageCompositeMasked",
                "inputs": {
                    "destination": ["9", 0],
                    "source": ["3", 0],
                    "x": 0,
                    "y": 0,
                    "resize_source": False,
                    # Feathered, so the photo fades into the generated margin
                    # over OUTPAINT_FEATHER pixels instead of meeting it on a
                    # hard line.
                    "mask": ["11", 0],
                },
            },
            "10": {
                "class_type": "SaveImage",
                "inputs": {"images": ["12", 0], "filename_prefix": output_prefix},
            },
        }

    def preflight(self) -> None:
        self.client.ping()

        installed = self.client.node_options("CheckpointLoaderSimple", "ckpt_name")
        if config.OUTPAINT_MODEL_FILE not in installed:
            raise RuntimeError(
                f"ComfyUI has no checkpoint named {config.OUTPAINT_MODEL_FILE!r} "
                f"(installed: {installed or 'none'}). Put a Stable Diffusion XL "
                f"checkpoint in {config.WAN_COMFYUI_DIR / 'models' / 'checkpoints'} "
                "and restart ComfyUI, or point OUTPAINT_MODEL_FILE at one you have."
            )

    def outpaint_to_frame(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        source_width, source_height = probe_image_size(image_path)
        margins = plan_margins(source_width, source_height, target_width, target_height)
        if margins.is_empty:
            # Already the frame's shape: generating here would spend a full
            # sampling pass to produce a resized copy of the input.
            return image_path

        # Checked per call rather than trusting the caller's earlier
        # preflight: the server can be stopped between scenes.
        self.preflight()

        image_filename = self.client.upload_image(image_path)
        output_prefix = Path(output_path).stem
        entry = self.client.run(
            self._build_prompt(
                image_filename, margins, prompt or config.OUTPAINT_DEFAULT_PROMPT, output_prefix
            )
        )

        return self.client.fetch_output(entry["outputs"]["10"]["images"][0], output_path)

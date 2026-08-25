"""Generated backgrounds for a pet photo, via ComfyUI + SDXL.

Two treatments, one graph shape (see pipeline/config.py's BACKGROUND_* block
for the product reasoning):

  extend   A landscape photo in a 9:16 video leaves two empty bands, and
           pipeline/editing.py can only fill them with a blurred copy of the
           photo, flat black, or a crop that throws away most of the picture.
           Here the bands are generated and the photo's own background is
           kept — nothing the camera saw is replaced.

  replace  BiRefNet cuts the pet out and the whole rest of the frame is
           generated, so the animal can be shown somewhere it has never
           been. The place is invented; the animal is not.

What never changes between them: the subject's real pixels are composited
back over the generated canvas at the end. The sampler works on a masked
region, but VAEDecode returns the *whole* canvas rebuilt from latents — the
photo included, softened and re-tinted by the round-trip — so without that
final composite "the pet is never redrawn" would be false in practice. The
only difference between the two treatments is which mask says "generate
here" and which says "keep the photograph".

Core ComfyUI nodes throughout (ImagePadForOutpaint / RemoveBackground /
VAEEncodeForInpaint / KSampler / ImageCompositeMasked) plus KJNodes'
GrowMaskWithBlur, which the Wan2.2 provider already requires. So the only
things to install are the two model files named in config.
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

#: Node ids in the graph below. Named because two treatments assemble the
#: same nodes with different mask wiring, and a bare "3" in both places is
#: how that stops being readable.
LOAD_IMAGE = "1"
SCALE = "2"
PAD = "3"
CHECKPOINT = "4"
POSITIVE = "5"
NEGATIVE = "6"
ENCODE = "7"
SAMPLER = "8"
DECODE = "9"
SAVE = "10"
#: InvertMask. Both treatments need one mask and its complement — which
#: of the two the sampler gets is the whole difference between them.
INVERTED_MASK = "11"
COMPOSITE = "12"
MATTE_MODEL = "13"
MATTE = "14"
SOLID_MATTE = "16"
SUBJECT_MASK = "15"


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
        is no margin to generate.

        Only "extend" can act on this — it means that treatment has nothing
        to do. "replace" still has the whole background to generate.
        """
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


class ComfyBackgroundProvider(ImageEditingProvider):
    """Both background treatments through the locally-hosted ComfyUI server
    that also runs Wan2.2 (see pipeline/config.py's WAN_* block for why that
    server exists). It must already be running — this provider does not spawn
    it (STARTUP.md).
    """

    def __init__(self, base_url: str | None = None):
        self.client = ComfyUIClient(base_url or config.BACKGROUND_COMFYUI_URL)

    def _common_nodes(
        self, image_filename: str, margins: OutpaintMargins, prompt: str, output_prefix: str
    ) -> dict:
        """Everything both treatments share: get the photo onto a frame-shaped
        canvas, load the models, encode the prompts, sample, decode, save.

        The two mask inputs the treatments differ on are left unwired here —
        _extend_graph and _replace_graph fill them in.
        """
        return {
            LOAD_IMAGE: {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            SCALE: {
                "class_type": "ImageScale",
                "inputs": {
                    "image": [LOAD_IMAGE, 0],
                    "upscale_method": "lanczos",
                    "width": margins.fit_width,
                    "height": margins.fit_height,
                    # "disabled": the aspect ratio was already preserved when
                    # plan_margins computed these two numbers, so cropping
                    # here would be the node second-guessing that.
                    "crop": "disabled",
                },
            },
            PAD: {
                "class_type": "ImagePadForOutpaint",
                "inputs": {
                    "image": [SCALE, 0],
                    "left": margins.left,
                    "top": margins.top,
                    "right": margins.right,
                    "bottom": margins.bottom,
                    "feathering": config.BACKGROUND_FEATHER,
                },
            },
            CHECKPOINT: {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": config.BACKGROUND_MODEL_FILE},
            },
            POSITIVE: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": [CHECKPOINT, 1]},
            },
            NEGATIVE: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": config.BACKGROUND_NEGATIVE_PROMPT, "clip": [CHECKPOINT, 1]},
            },
            ENCODE: {
                "class_type": "VAEEncodeForInpaint",
                "inputs": {
                    "pixels": [PAD, 0],
                    "vae": [CHECKPOINT, 2],
                    "grow_mask_by": config.BACKGROUND_GROW_MASK,
                },
            },
            SAMPLER: {
                "class_type": "KSampler",
                "inputs": {
                    "model": [CHECKPOINT, 0],
                    "seed": config.BACKGROUND_SEED,
                    "steps": config.BACKGROUND_STEPS,
                    "cfg": config.BACKGROUND_CFG,
                    "sampler_name": config.BACKGROUND_SAMPLER,
                    "scheduler": config.BACKGROUND_SCHEDULER,
                    "positive": [POSITIVE, 0],
                    "negative": [NEGATIVE, 0],
                    "latent_image": [ENCODE, 0],
                    # 1.0 because VAEEncodeForInpaint has already erased the
                    # masked area — there is no original content left there
                    # to preserve, and a lower value only leaves the erase
                    # showing through.
                    "denoise": 1.0,
                },
            },
            DECODE: {
                "class_type": "VAEDecode",
                "inputs": {"samples": [SAMPLER, 0], "vae": [CHECKPOINT, 2]},
            },
            COMPOSITE: {
                "class_type": "ImageCompositeMasked",
                "inputs": {
                    "destination": [DECODE, 0],
                    "source": [PAD, 0],
                    "x": 0,
                    "y": 0,
                    "resize_source": False,
                },
            },
            SAVE: {
                "class_type": "SaveImage",
                "inputs": {"images": [COMPOSITE, 0], "filename_prefix": output_prefix},
            },
        }

    def _extend_graph(
        self, image_filename: str, margins: OutpaintMargins, prompt: str, output_prefix: str
    ) -> dict:
        """Generate the margin, keep the photograph.

        ImagePadForOutpaint's second output is the mask of exactly what it
        added, so the sampler gets that and the composite gets its inverse.
        """
        graph = self._common_nodes(image_filename, margins, prompt, output_prefix)
        graph[ENCODE]["inputs"]["mask"] = [PAD, 1]
        graph[INVERTED_MASK] = {"class_type": "InvertMask", "inputs": {"mask": [PAD, 1]}}
        graph[COMPOSITE]["inputs"]["mask"] = [INVERTED_MASK, 0]
        return graph

    def _replace_graph(
        self, image_filename: str, margins: OutpaintMargins, prompt: str, output_prefix: str
    ) -> dict:
        """Generate everything except the pet.

        BiRefNet mattes the subject out of the padded canvas, the matte is
        softened, and the sampler gets its complement while the composite
        gets the matte itself — so the background is repainted and the animal
        is restored through the same softened edge. That blur is what stops
        the result reading as a sticker pasted on a picture.
        """
        graph = self._common_nodes(image_filename, margins, prompt, output_prefix)
        graph[MATTE_MODEL] = {
            "class_type": "LoadBackgroundRemovalModel",
            "inputs": {"bg_removal_name": config.BACKGROUND_MATTE_MODEL_FILE},
        }
        graph[MATTE] = {
            "class_type": "RemoveBackground",
            "inputs": {"bg_removal_model": [MATTE_MODEL, 0], "image": [PAD, 0]},
        }
        # BiRefNet returns confidences, not a binary matte, and a hazy photo
        # (one shot through glass, say) comes back around 0.5 across the whole
        # animal. Composited straight, that half-blends the pet with the
        # generated room and it appears as a ghost — measured on a real
        # asset. Thresholding first makes the subject solid; the blur below
        # then puts a soft edge back where it belongs, at the outline.
        graph[SOLID_MATTE] = {
            "class_type": "ThresholdMask",
            "inputs": {"mask": [MATTE, 0], "value": config.BACKGROUND_MATTE_THRESHOLD},
        }
        graph[SUBJECT_MASK] = {
            "class_type": "GrowMaskWithBlur",
            "inputs": {
                "mask": [SOLID_MATTE, 0],
                "expand": config.BACKGROUND_SUBJECT_GROW,
                "incremental_expandrate": 0.0,
                "tapered_corners": True,
                "flip_input": False,
                "blur_radius": config.BACKGROUND_SUBJECT_FEATHER,
                "lerp_alpha": 1.0,
                "decay_factor": 1.0,
                # Off: this node's hole-filling returned a fully white mask
                # for a clean BiRefNet matte (measured), which makes the
                # subject "everything" — the sampler then has nothing to
                # repaint and the photo comes back unchanged. BiRefNet's
                # matte is solid enough not to need it.
                "fill_holes": False,
            },
        }
        # The node does publish a second, inverted output, but it came back
        # empty in testing, so the complement is taken from core InvertMask
        # instead — one node, unambiguous semantics.
        graph[INVERTED_MASK] = {
            "class_type": "InvertMask",
            "inputs": {"mask": [SUBJECT_MASK, 0]},
        }
        graph[ENCODE]["inputs"]["mask"] = [INVERTED_MASK, 0]
        graph[COMPOSITE]["inputs"]["mask"] = [SUBJECT_MASK, 0]
        return graph

    def preflight(self, *, mode: str = "extend") -> None:
        self.client.ping()

        checkpoints = self.client.node_options("CheckpointLoaderSimple", "ckpt_name")
        if config.BACKGROUND_MODEL_FILE not in checkpoints:
            raise RuntimeError(
                f"ComfyUI has no checkpoint named {config.BACKGROUND_MODEL_FILE!r} "
                f"(installed: {checkpoints or 'none'}). Put a Stable Diffusion XL "
                f"checkpoint in {config.WAN_COMFYUI_DIR / 'models' / 'checkpoints'} "
                "and restart ComfyUI, or point BACKGROUND_MODEL_FILE at one you have."
            )

        if mode != "replace":
            # Matting weights are only loaded by the replace graph, so an
            # extend-only run must not be blocked on having them.
            return

        mattes = self.client.node_options("LoadBackgroundRemovalModel", "bg_removal_name")
        if config.BACKGROUND_MATTE_MODEL_FILE not in mattes:
            raise RuntimeError(
                f"ComfyUI has no background-removal model named "
                f"{config.BACKGROUND_MATTE_MODEL_FILE!r} (installed: {mattes or 'none'}) — "
                "replacing a background needs one to cut the pet out. Put BiRefNet in "
                f"{config.WAN_COMFYUI_DIR / 'models' / 'background_removal'} "
                "(Comfy-Org/BiRefNet on Hugging Face) and restart ComfyUI."
            )

    def _run(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None,
        output_path: str,
        mode: str,
    ) -> str:
        source_width, source_height = probe_image_size(image_path)
        margins = plan_margins(source_width, source_height, target_width, target_height)
        if mode == "extend" and margins.is_empty:
            # Already the frame's shape: generating here would spend a full
            # sampling pass to produce a resized copy of the input.
            return image_path

        # Checked per call rather than trusting the caller's earlier
        # preflight: the server can be stopped between scenes.
        self.preflight(mode=mode)

        image_filename = self.client.upload_image(image_path)
        output_prefix = Path(output_path).stem
        build = self._extend_graph if mode == "extend" else self._replace_graph
        entry = self.client.run(
            build(
                image_filename, margins, prompt or config.BACKGROUND_DEFAULT_PROMPT, output_prefix
            )
        )

        return self.client.fetch_output(entry["outputs"][SAVE]["images"][0], output_path)

    def outpaint_to_frame(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        return self._run(
            image_path,
            target_width=target_width,
            target_height=target_height,
            prompt=prompt,
            output_path=output_path,
            mode="extend",
        )

    def replace_background(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        return self._run(
            image_path,
            target_width=target_width,
            target_height=target_height,
            prompt=prompt,
            output_path=output_path,
            mode="replace",
        )

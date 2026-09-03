"""Generated backgrounds for a pet photo, via ComfyUI + SDXL.

Two treatments, one graph shape (see pipeline/config.py's BACKGROUND_* block
for the product reasoning):

  extend   A landscape photo in a 9:16 video leaves two empty bands, and
           pipeline/editing.py can only fill them with a blurred copy of the
           photo, flat black, or a crop that throws away most of the picture.
           Here the bands are generated and the photo's own background is
           kept — nothing the camera saw is replaced.

  replace  The pet is segmented out and the whole rest of the frame is
           generated, so the animal can be shown somewhere it has never
           been. The place is invented; the animal is not.

What never changes between them: the subject's real pixels are composited
back over the generated canvas at the end. The sampler works on a masked
region, but VAEDecode returns the *whole* canvas rebuilt from latents — the
photo included, softened and re-tinted by the round-trip — so without that
final composite "the pet is never redrawn" would be false in practice. The
only difference between the two treatments is which mask says "generate
here" and which says "keep the photograph".

Core ComfyUI nodes throughout (ImagePadForOutpaint / SAM3_Detect or
RemoveBackground / VAEEncodeForInpaint / KSampler / ImageCompositeMasked)
plus KJNodes' GrowMaskWithBlur, which the Wan2.2 provider already requires.
So the only things to install are the model files named in config.
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
SUBJECT_TEXT = "17"
MATTE_AS_IMAGE = "22"
SCALED_MATTE_IMAGE = "23"
SCALED_MATTE = "24"
EMPTY_CANVAS_MASK = "20"
PLACED_MATTE = "21"
SOLID_MATTE = "16"
SUBJECT_MASK = "15"
MASK_IMAGE = "18"
SAVE_MASK = "19"


# --- props ------------------------------------------------------------------
#
# Node ids for the prop graph. A separate range from the background graph's
# above: the two share a server and a checkpoint but not a single node, and
# reusing "3" for something else in both is how a graph stops being readable.
P_LOAD = "30"
P_CHECKPOINT = "31"
P_POSITIVE = "32"
P_NEGATIVE = "33"
P_MATTE_MODEL = "34"
P_SUBJECT_TEXT = "35"
P_MATTE = "36"
P_SUBJECT_MASK = "37"
P_REGION_BASE = "38"
P_REGION_PATCH = "39"
P_REGION = "40"
P_TARGET = "41"
P_SOFT_TARGET = "42"
P_ENCODE = "43"
P_CANNY = "44"
P_CONTROLNET = "45"
P_CONTROL_APPLY = "46"
P_SAMPLER = "47"
P_DECODE = "48"
P_INVERTED = "49"
P_COMPOSITE = "50"
P_SAVE = "51"
P_MASK_IMAGE = "52"
P_SAVE_MASK = "53"


def region_pixels(
    region: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    """Turn a fractional (left, top, right, bottom) into (x, y, w, h) pixels.

    Clamped to the image and to at least one pixel each way: the fractions
    come from a person dragging a box in a UI, and a reversed or off-canvas
    drag should produce a small region rather than a ComfyUI error about a
    negative SolidMask.
    """
    left, top, right, bottom = region
    x0 = int(max(0.0, min(1.0, min(left, right))) * width)
    y0 = int(max(0.0, min(1.0, min(top, bottom))) * height)
    x1 = int(max(0.0, min(1.0, max(left, right))) * width)
    y1 = int(max(0.0, min(1.0, max(top, bottom))) * height)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


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


def mask_coverage(mask_path: str) -> float:
    """Share of the frame a saved mask covers, 0.0-1.0.

    FFmpeg converts the mask to raw 8-bit grey and this averages the bytes.
    Two shortcuts were tried first and both were wrong in ways that made the
    check worse than useless:

    signalstats prints to stderr, and stderr came back empty when this ran
    inside the full pipeline (something in that process had already made
    captured output unavailable), turning a working check into a crash.

    Scaling the mask to a single pixel and reading that byte looked like a
    clean average and is not: a mask measured at 5.3% by signalstats read
    back as 0.4%, roughly thirteen times too low, which made the guard below
    reject perfectly good photographs. The scaler does not promise an
    average over a 720x1280-to-1x1 reduction.

    Averaging every byte is neither clever nor slow — a frame of this size
    is under a megabyte — and it is exactly the number being asked for.
    """
    raw_path = Path(mask_path).with_suffix(".gray.raw")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            mask_path,
            "-vf",
            "format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            str(raw_path),
        ],
        check=True,
    )
    pixels = raw_path.read_bytes()
    if not pixels:
        raise RuntimeError(f"FFmpeg produced no pixels for {mask_path}")
    return sum(pixels) / len(pixels) / 255.0


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

    def _matte_nodes(self, subject: str, margins: OutpaintMargins) -> dict:
        """Nodes producing a mask of the pet on the full frame-sized canvas.

        Segmentation runs on the photograph as uploaded — full resolution, no
        frame padding — and the mask it returns is then shrunk and placed
        where the photo will sit on the canvas. Both of those matter, and
        both were measured on the same real asset: a cat that segmented fine
        at full size came back as 0.0% of the frame once the grey padding
        bars were around it (a photo never has those, so the detector was
        being shown something unlike its training data), and 0.0% again when
        the photo was merely downscaled first (the animal was small in a
        cluttered room, and the detail it needed was gone).

        Both backends end at PLACED_MATTE output 0, so the rest of the
        replace graph does not care which one ran.
        """
        placement = {
            MATTE_AS_IMAGE: {"class_type": "MaskToImage", "inputs": {"mask": [MATTE, 0]}},
            SCALED_MATTE_IMAGE: {
                "class_type": "ImageScale",
                "inputs": {
                    "image": [MATTE_AS_IMAGE, 0],
                    "upscale_method": "bilinear",
                    "width": margins.fit_width,
                    "height": margins.fit_height,
                    "crop": "disabled",
                },
            },
            SCALED_MATTE: {
                "class_type": "ImageToMask",
                # The mask was written to all three channels by MaskToImage;
                # any one of them reads it back.
                "inputs": {"image": [SCALED_MATTE_IMAGE, 0], "channel": "red"},
            },
            EMPTY_CANVAS_MASK: {
                "class_type": "SolidMask",
                "inputs": {
                    "value": 0.0,
                    "width": margins.left + margins.fit_width + margins.right,
                    "height": margins.top + margins.fit_height + margins.bottom,
                },
            },
            PLACED_MATTE: {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [EMPTY_CANVAS_MASK, 0],
                    "source": [SCALED_MATTE, 0],
                    "x": margins.left,
                    "y": margins.top,
                    # The canvas is empty, so adding the matte onto it is a
                    # placement rather than a blend — the generated margin
                    # stays background, which is what it is.
                    "operation": "add",
                },
            },
        }

        if config.BACKGROUND_MATTE_BACKEND == "birefnet":
            return {
                MATTE_MODEL: {
                    "class_type": "LoadBackgroundRemovalModel",
                    "inputs": {"bg_removal_name": config.BACKGROUND_MATTE_MODEL_FILE},
                },
                MATTE: {
                    "class_type": "RemoveBackground",
                    "inputs": {"bg_removal_model": [MATTE_MODEL, 0], "image": [LOAD_IMAGE, 0]},
                },
                **placement,
            }

        # SAM3: told what to look for, so it returns the animal and not
        # whatever the animal happens to be sitting on.
        return {
            MATTE_MODEL: {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": config.BACKGROUND_SAM3_MODEL_FILE},
            },
            SUBJECT_TEXT: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": subject, "clip": [MATTE_MODEL, 1]},
            },
            MATTE: {
                "class_type": "SAM3_Detect",
                "inputs": {
                    "model": [MATTE_MODEL, 0],
                    "image": [LOAD_IMAGE, 0],
                    "conditioning": [SUBJECT_TEXT, 0],
                    "threshold": config.BACKGROUND_SAM3_THRESHOLD,
                    "refine_iterations": config.BACKGROUND_SAM3_REFINE_ITERATIONS,
                    # One mask covering everything it found, not one per
                    # object: a photo with two of the shelter's cats in it
                    # should keep both, and the rest of the graph takes a
                    # single mask.
                    "individual_masks": False,
                },
            },
            **placement,
        }

    def _replace_graph(
        self,
        image_filename: str,
        margins: OutpaintMargins,
        prompt: str,
        output_prefix: str,
        subject: str,
    ) -> dict:
        """Generate everything except the pet.

        The subject is matted out of the padded canvas, the matte is softened,
        and the sampler gets its complement while the composite gets the matte
        itself — so the background is repainted and the animal is restored
        through the same softened edge. That blur is what stops the result
        reading as a sticker pasted on a picture.
        """
        graph = self._common_nodes(image_filename, margins, prompt, output_prefix)
        graph.update(self._matte_nodes(subject, margins))
        # Matting returns confidences, not a binary mask, and a hazy photo
        # (one shot through glass, say) came back around 0.5 across the whole
        # animal. Composited straight, that half-blends the pet with the
        # generated room and it appears as a ghost — measured on a real
        # asset. Thresholding first makes the subject solid; the blur below
        # then puts a soft edge back where it belongs, at the outline.
        graph[SOLID_MATTE] = {
            "class_type": "ThresholdMask",
            "inputs": {"mask": [PLACED_MATTE, 0], "value": config.BACKGROUND_MATTE_THRESHOLD},
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
                # for a clean matte (measured), which makes the
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

        # Saved so the caller can check the subject was actually found before
        # accepting the result — see _run. It doubles as the record of what
        # was cut out, which is the first thing to look at when a replaced
        # shot comes out wrong.
        graph[MASK_IMAGE] = {"class_type": "MaskToImage", "inputs": {"mask": [SUBJECT_MASK, 0]}}
        graph[SAVE_MASK] = {
            "class_type": "SaveImage",
            "inputs": {"images": [MASK_IMAGE, 0], "filename_prefix": f"{output_prefix}_mask"},
        }
        return graph

    def _prop_matte_nodes(self, subject: str) -> dict:
        """A mask of the animal, on the photograph at its own size.

        Simpler than the background graph's equivalent: nothing is padded or
        rescaled here, because a prop is painted onto the photo as it was
        taken and the framing work happens later in the pipeline. So the
        matte needs no placing onto a larger canvas — it is already in
        register with the image the sampler sees.
        """
        if config.BACKGROUND_MATTE_BACKEND == "birefnet":
            return {
                P_MATTE_MODEL: {
                    "class_type": "LoadBackgroundRemovalModel",
                    "inputs": {"bg_removal_name": config.BACKGROUND_MATTE_MODEL_FILE},
                },
                P_MATTE: {
                    "class_type": "RemoveBackground",
                    "inputs": {"bg_removal_model": [P_MATTE_MODEL, 0], "image": [P_LOAD, 0]},
                },
            }

        return {
            P_MATTE_MODEL: {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": config.BACKGROUND_SAM3_MODEL_FILE},
            },
            P_SUBJECT_TEXT: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": subject, "clip": [P_MATTE_MODEL, 1]},
            },
            P_MATTE: {
                "class_type": "SAM3_Detect",
                "inputs": {
                    "model": [P_MATTE_MODEL, 0],
                    "image": [P_LOAD, 0],
                    "conditioning": [P_SUBJECT_TEXT, 0],
                    "threshold": config.BACKGROUND_SAM3_THRESHOLD,
                    "refine_iterations": config.BACKGROUND_SAM3_REFINE_ITERATIONS,
                    "individual_masks": False,
                },
            },
        }

    def _prop_graph(
        self,
        image_filename: str,
        *,
        region: tuple[float, float, float, float],
        on_subject: bool,
        prompt: str,
        subject: str,
        width: int,
        height: int,
        output_prefix: str,
    ) -> dict:
        """Paint one object into one region of the photograph.

        The shape of it: the reviewer's rectangle is turned into a mask, met
        with the animal's own silhouette, softened, and handed to an inpaint
        sampler that is additionally held to the photograph's edges by a
        Canny ControlNet. Everything outside that mask is composited straight
        back from the original.

        Three parts are worth explaining because a reader will otherwise
        assume the obvious version:

        The rectangle alone is not the mask. A collar painted into a bare
        rectangle spills onto whatever is behind the animal, so the region is
        multiplied by the subject mask — the paint can only land on the
        animal. A toy is the other way round: subtracted, so it is placed
        beside the animal rather than through it. That is the whole meaning
        of on_subject, and it is why this cannot be done with a plain
        rectangular inpaint.

        ControlNet is not what protects the animal's face. The composite at
        the end does that, and did before this existed: pixels outside the
        mask are the original's, full stop. What Canny buys is a prop that
        follows the body's real edges instead of sitting on top of them —
        conformity, not safety. Strength is deliberately below 1.0 so it
        guides rather than dictates; at 1.0 the sampler tries to reproduce
        the edge map exactly and paints an outline rather than an object.

        The subject mask is saved as its own output. It is the only way to
        tell afterwards whether the segmenter actually found the animal, and
        a prop painted where there is no animal is the failure this shares
        with a replaced background.
        """
        x, y, region_width, region_height = region_pixels(region, width, height)
        return {
            P_LOAD: {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            P_CHECKPOINT: {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": config.BACKGROUND_MODEL_FILE},
            },
            P_POSITIVE: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": [P_CHECKPOINT, 1]},
            },
            P_NEGATIVE: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": config.PROPS_NEGATIVE_PROMPT, "clip": [P_CHECKPOINT, 1]},
            },
            **self._prop_matte_nodes(subject),
            P_SUBJECT_MASK: {
                # Matting returns confidences; the region arithmetic below is
                # set operations, which need a yes/no mask. Same reason the
                # replace graph thresholds before compositing.
                "class_type": "ThresholdMask",
                "inputs": {"mask": [P_MATTE, 0], "value": config.BACKGROUND_MATTE_THRESHOLD},
            },
            P_REGION_BASE: {
                "class_type": "SolidMask",
                "inputs": {"value": 0.0, "width": width, "height": height},
            },
            P_REGION_PATCH: {
                "class_type": "SolidMask",
                "inputs": {"value": 1.0, "width": region_width, "height": region_height},
            },
            P_REGION: {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [P_REGION_BASE, 0],
                    "source": [P_REGION_PATCH, 0],
                    "x": x,
                    "y": y,
                    "operation": "add",
                },
            },
            P_TARGET: {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [P_REGION, 0],
                    "source": [P_SUBJECT_MASK, 0],
                    "x": 0,
                    "y": 0,
                    # "multiply" is the intersection: paint only where the
                    # reviewer's box and the animal overlap. "subtract" is the
                    # complement within the box: beside the animal, never on
                    # it.
                    "operation": "multiply" if on_subject else "subtract",
                },
            },
            P_SOFT_TARGET: {
                "class_type": "GrowMaskWithBlur",
                "inputs": {
                    "mask": [P_TARGET, 0],
                    "expand": config.PROPS_MASK_GROW,
                    "incremental_expandrate": 0.0,
                    "tapered_corners": True,
                    "flip_input": False,
                    "blur_radius": config.PROPS_MASK_FEATHER,
                    "lerp_alpha": 1.0,
                    "decay_factor": 1.0,
                    # Off: on a clean matte this returns solid white, which
                    # would mean "the whole frame is the region" and repaint
                    # the entire photograph. Measured on the background graph,
                    # and the same node behaves the same way here.
                    "fill_holes": False,
                },
            },
            P_ENCODE: {
                "class_type": "VAEEncodeForInpaint",
                "inputs": {
                    "pixels": [P_LOAD, 0],
                    "vae": [P_CHECKPOINT, 2],
                    "mask": [P_SOFT_TARGET, 0],
                    "grow_mask_by": config.PROPS_MASK_GROW,
                },
            },
            P_CANNY: {
                "class_type": "Canny",
                "inputs": {
                    "image": [P_LOAD, 0],
                    "low_threshold": config.PROPS_CANNY_LOW,
                    "high_threshold": config.PROPS_CANNY_HIGH,
                },
            },
            P_CONTROLNET: {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": config.PROPS_CONTROLNET_FILE},
            },
            P_CONTROL_APPLY: {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "positive": [P_POSITIVE, 0],
                    "negative": [P_NEGATIVE, 0],
                    "control_net": [P_CONTROLNET, 0],
                    "image": [P_CANNY, 0],
                    "strength": config.PROPS_CONTROLNET_STRENGTH,
                    "start_percent": 0.0,
                    # Released partway so the later steps are free to form the
                    # prop's own edges, which are not in the photograph and so
                    # are not in the hint. Measured: held to 0.8 at strength
                    # 0.65, no collar appeared at all.
                    "end_percent": config.PROPS_CONTROLNET_END,
                },
            },
            P_SAMPLER: {
                "class_type": "KSampler",
                "inputs": {
                    "model": [P_CHECKPOINT, 0],
                    "seed": config.PROPS_SEED,
                    "steps": config.PROPS_STEPS,
                    "cfg": config.PROPS_CFG,
                    "sampler_name": config.BACKGROUND_SAMPLER,
                    "scheduler": config.BACKGROUND_SCHEDULER,
                    "positive": [P_CONTROL_APPLY, 0],
                    "negative": [P_CONTROL_APPLY, 1],
                    "latent_image": [P_ENCODE, 0],
                    "denoise": config.PROPS_DENOISE,
                },
            },
            P_DECODE: {
                "class_type": "VAEDecode",
                "inputs": {"samples": [P_SAMPLER, 0], "vae": [P_CHECKPOINT, 2]},
            },
            P_INVERTED: {"class_type": "InvertMask", "inputs": {"mask": [P_SOFT_TARGET, 0]}},
            P_COMPOSITE: {
                "class_type": "ImageCompositeMasked",
                "inputs": {
                    "destination": [P_DECODE, 0],
                    "source": [P_LOAD, 0],
                    "x": 0,
                    "y": 0,
                    "resize_source": False,
                    # Everything the sampler was not asked to touch comes back
                    # from the photograph. VAEDecode returns the whole frame
                    # rebuilt from latents — softer and colour-shifted — so
                    # without this the "only the region changes" promise is
                    # just words.
                    "mask": [P_INVERTED, 0],
                },
            },
            P_MASK_IMAGE: {"class_type": "MaskToImage", "inputs": {"mask": [P_SUBJECT_MASK, 0]}},
            P_SAVE_MASK: {
                "class_type": "SaveImage",
                "inputs": {"images": [P_MASK_IMAGE, 0], "filename_prefix": output_prefix + "_mask"},
            },
            P_SAVE: {
                "class_type": "SaveImage",
                "inputs": {"images": [P_COMPOSITE, 0], "filename_prefix": output_prefix},
            },
        }

    def preflight_props(self) -> None:
        """Raise if a prop run clearly cannot start.

        Its own method rather than another `mode` on preflight(): props need
        the ControlNet that neither background treatment loads, and a
        background run must not be refused for missing it.
        """
        self.client.ping()

        checkpoints = self.client.node_options("CheckpointLoaderSimple", "ckpt_name")
        if config.BACKGROUND_MODEL_FILE not in checkpoints:
            raise RuntimeError(
                f"ComfyUI has no checkpoint named {config.BACKGROUND_MODEL_FILE!r} "
                f"(installed: {checkpoints or 'none'}). Put a Stable Diffusion XL "
                f"checkpoint in {config.WAN_COMFYUI_DIR / 'models' / 'checkpoints'} "
                "and restart ComfyUI."
            )

        controlnets = self.client.node_options("ControlNetLoader", "control_net_name")
        if config.PROPS_CONTROLNET_FILE not in controlnets:
            raise RuntimeError(
                f"ComfyUI has no ControlNet named {config.PROPS_CONTROLNET_FILE!r} "
                f"(installed: {controlnets or 'none'}) — adding a prop needs one to hold "
                "the animal's own edges while the region is repainted. Put an SDXL Canny "
                f"ControlNet in {config.WAN_COMFYUI_DIR / 'models' / 'controlnet'} "
                "(diffusers/controlnet-canny-sdxl-1.0 on Hugging Face) and restart ComfyUI."
            )

        if config.BACKGROUND_MATTE_BACKEND == "birefnet":
            mattes = self.client.node_options("LoadBackgroundRemovalModel", "bg_removal_name")
            if config.BACKGROUND_MATTE_MODEL_FILE not in mattes:
                raise RuntimeError(
                    f"ComfyUI has no background-removal model named "
                    f"{config.BACKGROUND_MATTE_MODEL_FILE!r} (installed: {mattes or 'none'}) — "
                    "a prop has to be placed against the animal's own outline. Put BiRefNet in "
                    f"{config.WAN_COMFYUI_DIR / 'models' / 'background_removal'} and restart."
                )
        elif config.BACKGROUND_SAM3_MODEL_FILE not in checkpoints:
            raise RuntimeError(
                f"ComfyUI has no checkpoint named {config.BACKGROUND_SAM3_MODEL_FILE!r} "
                f"(installed: {checkpoints or 'none'}) — a prop has to be placed against the "
                f"animal's own outline. Put SAM3 in "
                f"{config.WAN_COMFYUI_DIR / 'models' / 'checkpoints'} and restart ComfyUI, "
                "or set BACKGROUND_MATTE_BACKEND=birefnet."
            )

    def add_prop(
        self,
        image_path: str,
        *,
        region: tuple[float, float, float, float],
        on_subject: bool,
        prompt: str | None = None,
        output_path: str,
        subject: str | None = None,
    ) -> str:
        width, height = probe_image_size(image_path)
        uploaded = self.client.upload_image(image_path)
        output_prefix = Path(output_path).stem

        graph = self._prop_graph(
            uploaded,
            region=region,
            on_subject=on_subject,
            prompt=prompt or config.PROPS_COLLAR_PROMPT,
            subject=subject or config.BACKGROUND_SUBJECT_FALLBACK,
            width=width,
            height=height,
            output_prefix=output_prefix,
        )
        entry = self.client.run(graph)

        # Checked before the picture is fetched: if the animal was never
        # found, the region the reviewer named is not on any animal and the
        # result is a prop lying on an empty floor.
        mask_path = Path(output_path).with_suffix(".mask.png")
        self.client.fetch_output(entry["outputs"][P_SAVE_MASK]["images"][0], str(mask_path))
        coverage = mask_coverage(str(mask_path))
        if coverage < config.PROPS_MIN_SUBJECT_COVERAGE:
            raise RuntimeError(
                f"Nothing matching {subject or config.BACKGROUND_SUBJECT_FALLBACK!r} was found "
                f"in {image_path} (it covers {coverage:.2%} of the frame, below "
                f"{config.PROPS_MIN_SUBJECT_COVERAGE:.2%}), so the prop would have been painted "
                f"onto an empty picture. Check that this asset is a photo of the pet. The mask "
                f"that was produced is at {mask_path}."
            )

        return self.client.fetch_output(entry["outputs"][P_SAVE]["images"][0], output_path)

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
            # The matting model is only loaded by the replace graph, so an
            # extend-only run must not be blocked on having it.
            return

        if config.BACKGROUND_MATTE_BACKEND == "birefnet":
            mattes = self.client.node_options("LoadBackgroundRemovalModel", "bg_removal_name")
            if config.BACKGROUND_MATTE_MODEL_FILE not in mattes:
                raise RuntimeError(
                    f"ComfyUI has no background-removal model named "
                    f"{config.BACKGROUND_MATTE_MODEL_FILE!r} (installed: {mattes or 'none'}) — "
                    "replacing a background needs one to cut the pet out. Put BiRefNet in "
                    f"{config.WAN_COMFYUI_DIR / 'models' / 'background_removal'} "
                    "(Comfy-Org/BiRefNet on Hugging Face) and restart ComfyUI."
                )
        elif config.BACKGROUND_SAM3_MODEL_FILE not in checkpoints:
            raise RuntimeError(
                f"ComfyUI has no checkpoint named {config.BACKGROUND_SAM3_MODEL_FILE!r} "
                f"(installed: {checkpoints or 'none'}) — replacing a background needs SAM3 "
                "to cut the pet out. Put it in "
                f"{config.WAN_COMFYUI_DIR / 'models' / 'checkpoints'} "
                "(Comfy-Org/sam3.1 on Hugging Face) and restart ComfyUI, or set "
                "BACKGROUND_MATTE_BACKEND=birefnet."
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
        subject: str | None = None,
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
        full_prompt = prompt or config.BACKGROUND_DEFAULT_PROMPT
        if mode == "extend":
            graph = self._extend_graph(image_filename, margins, full_prompt, output_prefix)
        else:
            graph = self._replace_graph(
                image_filename,
                margins,
                full_prompt,
                output_prefix,
                subject or config.BACKGROUND_SUBJECT_FALLBACK,
            )
        entry = self.client.run(graph)

        if mode == "replace":
            self._require_subject_was_found(entry, image_path, output_path, subject)

        return self.client.fetch_output(entry["outputs"][SAVE]["images"][0], output_path)

    def _require_subject_was_found(
        self, entry: dict, image_path: str, output_path: str, subject: str | None
    ) -> None:
        """Refuse a replaced shot whose subject mask is effectively empty.

        The failure this exists for is silent: told to find a cat in a photo
        that has none, the segmenter correctly returns nothing, the sampler
        is then free to repaint the whole frame, and the shot comes back as
        scenery with no animal in it. Better to stop and name the asset.
        """
        mask_path = Path(output_path).with_suffix(".mask.png")
        self.client.fetch_output(entry["outputs"][SAVE_MASK]["images"][0], str(mask_path))

        coverage = mask_coverage(str(mask_path))
        if coverage < config.BACKGROUND_MIN_SUBJECT_COVERAGE:
            raise RuntimeError(
                f"Nothing matching {subject or config.BACKGROUND_SUBJECT_FALLBACK!r} was found "
                f"in {image_path} (it covers {coverage:.2%} of the frame, below "
                f"{config.BACKGROUND_MIN_SUBJECT_COVERAGE:.2%}), so replacing the background "
                f"would have produced a shot with no animal in it. Check that this asset is "
                f"a photo of the pet, or use --background-mode extend for it. The mask that "
                f"was produced is at {mask_path}."
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
        subject: str | None = None,
    ) -> str:
        return self._run(
            image_path,
            target_width=target_width,
            target_height=target_height,
            prompt=prompt,
            output_path=output_path,
            mode="replace",
            subject=subject,
        )

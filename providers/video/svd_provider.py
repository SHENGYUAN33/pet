from __future__ import annotations

from pipeline import config
from providers.base import VideoGenerationProvider


class SVDProvider(VideoGenerationProvider):
    """Open-source Image-to-Video via Stable Video Diffusion
    (stabilityai/stable-video-diffusion-img2vid-xt). Lazy-loads the
    pipeline onto the GPU on first use (heavy import/load), mirroring
    providers/tts/xtts_provider.py. Uses enable_model_cpu_offload rather
    than a plain .to("cuda") — the full pipeline is a tight fit on a 16GB
    card, offloading unused submodules to system RAM trades some speed for
    headroom."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.SVD_MODEL_NAME
        self._pipe = None

    def _load(self):
        if self._pipe is None:
            import torch
            from diffusers import StableVideoDiffusionPipeline

            self._pipe = StableVideoDiffusionPipeline.from_pretrained(
                self.model_name, torch_dtype=torch.float16, variant="fp16"
            )
            self._pipe.enable_model_cpu_offload()
        return self._pipe

    def animate_image(self, image_path: str, *, duration_seconds: float, output_path: str) -> str:
        from diffusers.utils import export_to_video
        from PIL import Image

        pipe = self._load()
        # SVD-XT was trained at 1024x576; our own crop/scale step in
        # editing.build_scene_clip normalizes whatever comes out of here
        # to the final 9:16 frame, so exact framing here doesn't matter.
        image = Image.open(image_path).convert("RGB").resize((1024, 576))
        frames = pipe(image, decode_chunk_size=8).frames[0]
        export_to_video(frames, output_path, fps=7)
        return output_path

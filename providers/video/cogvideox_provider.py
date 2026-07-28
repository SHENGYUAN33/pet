from __future__ import annotations

from pipeline import config
from providers.base import VideoGenerationProvider


class CogVideoXProvider(VideoGenerationProvider):
    """Open-source Image-to-Video via CogVideoX. Note: the officially
    released image-to-video checkpoint is the 5B variant
    (THUDM/CogVideoX-5b-I2V) — there is no 2B I2V checkpoint, despite
    "CogVideoX-2B" being the commonly cited name for the text-to-video
    line. Heavier/slower than SVD (a single clip can take minutes rather
    than seconds on a 16GB card) but tends to produce more natural motion.
    Lazy-loads onto the GPU on first use."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.COGVIDEOX_MODEL_NAME
        self._pipe = None

    def _load(self):
        if self._pipe is None:
            import torch
            from diffusers import CogVideoXImageToVideoPipeline

            self._pipe = CogVideoXImageToVideoPipeline.from_pretrained(
                self.model_name, torch_dtype=torch.bfloat16
            )
            self._pipe.enable_model_cpu_offload()
            self._pipe.vae.enable_tiling()
        return self._pipe

    def animate_image(self, image_path: str, *, duration_seconds: float, output_path: str) -> str:
        from diffusers.utils import export_to_video
        from PIL import Image

        pipe = self._load()
        image = Image.open(image_path).convert("RGB")
        frames = pipe(image=image, prompt=config.COGVIDEOX_DEFAULT_PROMPT).frames[0]
        export_to_video(frames, output_path, fps=8)
        return output_path

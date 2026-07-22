from pipeline import config
from providers.base import TTSProvider


class XTTSProvider(TTSProvider):
    """Open-source TTS via Coqui XTTS-v2, zero-shot voice cloning from a
    reference wav (voice_profile). Model load is lazy and cached on first use
    since it is a heavy (GPU) dependency."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.XTTS_MODEL_NAME
        self._tts = None

    def _load(self):
        if self._tts is None:
            from TTS.api import TTS  # heavy import, deferred until actually needed

            self._tts = TTS(self.model_name)
        return self._tts

    def synthesize(
        self, text: str, *, voice_profile: str, language: str, output_path: str
    ) -> str:
        tts = self._load()
        tts.tts_to_file(
            text=text,
            speaker_wav=voice_profile,
            language=language or config.TTS_LANGUAGE,
            file_path=output_path,
        )
        return output_path

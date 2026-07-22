from __future__ import annotations

from pathlib import Path

from pipeline import config
from providers.base import TTSProvider


def synthesize_scenes(
    script: dict, tts: TTSProvider, *, voice_profile: str, output_dir: Path
) -> dict[int, str]:
    """Synthesize one narration wav per scene, keyed by scene_id."""
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_paths: dict[int, str] = {}
    for scene in script["scenes"]:
        out_path = output_dir / f"scene_{scene['scene_id']}.wav"
        tts.synthesize(
            scene["narration"],
            voice_profile=voice_profile,
            language=config.TTS_LANGUAGE,
            output_path=str(out_path),
        )
        audio_paths[scene["scene_id"]] = str(out_path)
    return audio_paths

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline import config
from providers.base import TTSProvider


def _write_silence(duration: float, out_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            str(duration),
            str(out_path),
        ],
        check=True,
    )


def synthesize_scenes(
    script: dict, tts: TTSProvider, *, voice_profile: str, output_dir: Path
) -> dict[int, str]:
    """Synthesize one narration wav per scene, keyed by scene_id.

    A scene with no narration gets silence of its own length instead of an
    empty TTS call — the closing recap (pipeline/montage.py) is deliberately
    wordless, and asking a voice model to read "" is not a sensible request.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_paths: dict[int, str] = {}
    for scene in script["scenes"]:
        out_path = output_dir / f"scene_{scene['scene_id']}.wav"
        if (scene.get("narration") or "").strip():
            tts.synthesize(
                scene["narration"],
                voice_profile=voice_profile,
                language=config.TTS_LANGUAGE,
                output_path=str(out_path),
            )
        else:
            _write_silence(scene["end"] - scene["start"], out_path)
        audio_paths[scene["scene_id"]] = str(out_path)
    return audio_paths


def silence_scenes(script: dict, output_dir: Path) -> dict[int, str]:
    """Placeholder silent audio per scene, for testing the visual/editing
    pipeline before a voice reference sample is available (no narration)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_paths: dict[int, str] = {}
    for scene in script["scenes"]:
        out_path = output_dir / f"scene_{scene['scene_id']}.wav"
        _write_silence(scene["end"] - scene["start"], out_path)
        audio_paths[scene["scene_id"]] = str(out_path)
    return audio_paths

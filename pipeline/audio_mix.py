from __future__ import annotations

import subprocess


def mix_narration_with_music(
    *,
    narration_path: str,
    music_path: str,
    duration: float,
    output_path: str,
    music_volume: float = 0.25,
) -> str:
    """Mix narration over background music with sidechain ducking (music
    volume drops automatically while narration is speaking) and loudness
    normalization, per docs/architecture.md §6 mixing principles: narration
    stays clearest, music ducks under it, output is loudness-normalized.

    Music is looped/trimmed to match the narration duration so any
    royalty-free track can be used regardless of its own length.
    """
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{duration},volume={music_volume}[music];"
        "[music][0:a]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=300[ducked];"
        "[0:a][ducked]amix=inputs=2:duration=first:weights=1 1,loudnorm[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", narration_path,
        "-i", music_path,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-t", str(duration),
        output_path,
    ]
    subprocess.run(cmd, check=True)
    return output_path

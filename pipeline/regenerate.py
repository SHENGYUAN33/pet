from __future__ import annotations

import argparse
import sys

from pipeline.regen import regenerate_scene

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate a single scene of a previous generation job "
        "without re-running script generation"
    )
    parser.add_argument("job_id", type=int, help="Generation job id (see pipeline.manage show-pet)")
    parser.add_argument("scene_id", type=int, help="Scene id within that job's script")
    parser.add_argument(
        "--visual-source", default=None, help="New asset_id/filename for this scene"
    )
    parser.add_argument("--subtitle", default=None, help="New subtitle text for this scene")
    parser.add_argument("--narration", default=None, help="New narration text for this scene")
    parser.add_argument(
        "--voice-sample",
        default=None,
        help="Reference wav for TTS voice cloning; pass again if the original job used one",
    )
    parser.add_argument(
        "--music-track",
        default=None,
        help="Background music file; pass again if the original job used one",
    )
    args = parser.parse_args()

    output_path, new_job_id = regenerate_scene(
        args.job_id,
        args.scene_id,
        visual_source=args.visual_source,
        subtitle=args.subtitle,
        narration=args.narration,
        voice_sample=args.voice_sample,
        music_track=args.music_track,
    )
    print(f"New job id: {new_job_id} (parent: {args.job_id})")
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

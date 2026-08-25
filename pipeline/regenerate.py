from __future__ import annotations

import argparse
import sys

from pipeline.regen import regenerate_scene

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--animate",
        action="store_true",
        help="Animate this scene's photo via Image-to-Video instead of Ken Burns "
        "(docs/architecture.md §5 strategy B — only applies if the scene's "
        "visual_source is a photo)",
    )
    parser.add_argument(
        "--video-provider",
        default="svd",
        choices=["svd", "cogvideox", "wan"],
        help="Which open-source I2V model to use with --animate (default: svd)",
    )
    parser.add_argument(
        "--outpaint",
        action="store_true",
        help="Fill this scene's empty frame margin with AI-generated surroundings "
        "instead of blurred bars (only applies if the scene's visual_source is a photo)",
    )
    parser.add_argument(
        "--outpaint-prompt",
        default=None,
        help="What the generated surroundings should be, in ENGLISH and describing "
        "the whole picture (SDXL's text encoder does not understand Chinese), e.g. "
        "'a grey cat resting in a cosy living room, warm afternoon light, realistic photograph'",
    )
    parser.add_argument(
        "--image-provider",
        default="comfy",
        choices=["comfy"],
        help="Which image provider fills the margin for --outpaint (default: comfy)",
    )
    parser.add_argument(
        "--animate-prompt",
        default=None,
        help="Motion guidance for the animated scene, e.g. '貓輕輕搖尾巴、抬頭看鏡頭' "
        "(only affects prompt-conditioned providers: cogvideox, wan; ignored by svd)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    output_path, new_job_id = regenerate_scene(
        args.job_id,
        args.scene_id,
        visual_source=args.visual_source,
        subtitle=args.subtitle,
        narration=args.narration,
        voice_sample=args.voice_sample,
        music_track=args.music_track,
        animate=args.animate,
        video_provider=args.video_provider,
        animate_prompt=args.animate_prompt,
        outpaint=args.outpaint,
        image_provider=args.image_provider,
        outpaint_prompt=args.outpaint_prompt,
    )
    print(f"New job id: {new_job_id} (parent: {args.job_id})")
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()

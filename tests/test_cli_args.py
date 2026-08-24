"""The CLI flags the docs tell people to type must be the flags the CLI has.

CLAUDE.md, STARTUP.md and .claude/commands/gen-video.md all document
--animate-prompt, and the web API's request field is animate_prompt too; the
parsers used to accept --prompt, so a copy-pasted documented command failed
outright. These parse the documented form and assert it reaches the argument
the pipeline functions are actually called with.
"""

from __future__ import annotations

import pipeline.regenerate as regenerate_cli
import pipeline.run as run_cli

MOTION = "貓輕輕搖尾巴、抬頭看鏡頭"


def test_run_accepts_documented_animate_prompt_flag():
    args = run_cli.build_parser().parse_args(
        [
            "--pet-id",
            "PET-2026-001",
            "--animate-scenes",
            "2,4",
            "--video-provider",
            "wan",
            "--animate-prompt",
            MOTION,
        ]
    )

    assert args.animate_prompt == MOTION
    assert args.video_provider == "wan"
    assert args.animate_scenes == "2,4"


def test_regenerate_accepts_documented_animate_prompt_flag():
    args = regenerate_cli.build_parser().parse_args(
        ["7", "3", "--animate", "--video-provider", "wan", "--animate-prompt", MOTION]
    )

    assert args.animate_prompt == MOTION
    assert args.animate is True
    assert (args.job_id, args.scene_id) == (7, 3)


def test_animate_prompt_defaults_to_none():
    """Omitting it is the default path (real footage + Ken Burns), not an error."""
    assert run_cli.build_parser().parse_args(["--pet-id", "PET-2026-001"]).animate_prompt is None
    assert regenerate_cli.build_parser().parse_args(["7", "3"]).animate_prompt is None

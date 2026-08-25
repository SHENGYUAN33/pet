"""What a given video length can actually hold.

The scene rules (docs/architecture.md §2: 5-7 shots, 3-6 seconds each, one
source asset per shot) decide how many of a pet's assets a video can use —
but until the script exists, only these numbers know that. Uploading twelve
photos for a 30-second video and finding out afterwards that only seven of
them made it is not something a reviewer should have to work out by hand,
so this computes it up front for the generate form to show.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from pipeline import config


class ScenePlan(BaseModel):
    """How many shots a video of this length can have, and therefore how many
    assets it can show."""

    duration: int
    fewest_scenes: int
    most_scenes: int
    #: False when no shot count satisfies both the 5-7 rule and the 3-6s rule
    #: — the script will come back with a timeline that doesn't add up.
    feasible: bool
    #: Shortest and longest duration that is feasible, so the form can say
    #: what to change the length to.
    shortest_feasible: int
    longest_feasible: int
    asset_count: int
    #: Assets this video can show at most, and how many are left over.
    usable_assets: int
    unused_assets: int
    #: True when there are fewer assets than the shot count needs, so some
    #: will have to appear more than once.
    assets_will_repeat: bool


def scene_budget(duration: int) -> tuple[int, int]:
    """(fewest, most) shots that can make up a video of this length.

    Both rules apply at once: the shot count has to stay within MIN/MAX_SCENES
    and every shot has to last MIN/MAX_SCENE_DURATION, so a 30s video is 5-7
    shots while a 15s one can only be 5. Returns fewest > most when the length
    cannot be built at all.
    """
    fewest = max(config.MIN_SCENES, math.ceil(duration / config.MAX_SCENE_DURATION))
    most = min(config.MAX_SCENES, duration // config.MIN_SCENE_DURATION)
    return fewest, most


def plan_scenes(duration: int, asset_count: int) -> ScenePlan:
    """Answer, before any LLM call, how much of this pet's media the video
    will actually be able to show."""
    fewest, most = scene_budget(duration)
    feasible = fewest <= most

    usable = min(asset_count, most) if feasible else 0
    return ScenePlan(
        duration=duration,
        fewest_scenes=fewest,
        most_scenes=most,
        feasible=feasible,
        shortest_feasible=config.MIN_SCENES * config.MIN_SCENE_DURATION,
        longest_feasible=config.MAX_SCENES * config.MAX_SCENE_DURATION,
        asset_count=asset_count,
        usable_assets=usable,
        unused_assets=max(0, asset_count - usable) if feasible else asset_count,
        assets_will_repeat=feasible and asset_count < fewest,
    )

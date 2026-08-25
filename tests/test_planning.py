"""What a video length can hold, computed before any LLM call.

The failure this prevents: uploading thirteen photos, generating a 30-second
video, and only then discovering that six of them were never going to fit.
"""

from __future__ import annotations

from pipeline import config
from pipeline.planning import plan_scenes, scene_budget


def test_thirty_seconds_is_five_to_seven_shots():
    assert scene_budget(30) == (config.MIN_SCENES, config.MAX_SCENES)


def test_fifteen_seconds_pins_the_shot_count():
    """15s can only be 5 shots: fewer would break the 6s-per-shot ceiling,
    more would break the 3s floor."""
    fewest, most = scene_budget(15)
    assert fewest == most == 5


def test_a_length_no_shot_count_can_build_is_reported_as_infeasible():
    """5-7 shots of 3-6s each cover 15-42s; 60s is outside that, and the
    script comes back with a timeline that doesn't add up."""
    plan = plan_scenes(60, asset_count=13)

    assert plan.feasible is False
    assert plan.shortest_feasible == config.MIN_SCENES * config.MIN_SCENE_DURATION
    assert plan.longest_feasible == config.MAX_SCENES * config.MAX_SCENE_DURATION


def test_surplus_assets_are_counted_as_unused():
    plan = plan_scenes(30, asset_count=13)

    assert plan.feasible is True
    assert plan.usable_assets == config.MAX_SCENES
    assert plan.unused_assets == 13 - config.MAX_SCENES
    assert plan.assets_will_repeat is False


def test_too_few_assets_means_some_will_repeat():
    plan = plan_scenes(30, asset_count=3)

    assert plan.assets_will_repeat is True
    assert plan.unused_assets == 0


def test_exactly_enough_assets_leaves_nothing_unused():
    plan = plan_scenes(30, asset_count=config.MAX_SCENES)

    assert plan.unused_assets == 0
    assert plan.assets_will_repeat is False

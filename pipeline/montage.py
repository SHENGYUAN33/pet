"""A closing recap for the assets the script had no room for.

A 30-second video is 5-7 shots and each shot shows one asset, so a pet with
thirteen photos has six that never appear — and someone who uploaded thirteen
photos meant all thirteen to be seen. Lengthening the story shots isn't the
answer: they're paced to one line of narration each (see script_gen's 22-character
rule), and stretching them to fit would leave the narration trailing silence.

So the leftovers get their own shot at the end — a quick cut, no narration,
carrying the CTA subtitle. The story keeps its pacing and every asset appears.

Implemented as a scene appended to the script rather than as a special case
inside rendering: that way narration, per-scene job rows, resume and subtitle
burn-in all treat it as the shot it is, with no parallel code path.
"""

from __future__ import annotations

from pipeline import config
from pipeline.profile import MediaAsset, PetProfile

#: purpose value marking the appended shot, so downstream code (and a human
#: reading the script JSON) can tell it apart from the LLM's own scenes.
RECAP_PURPOSE = "recap"


def scene_sources(scene: dict) -> list[str]:
    """Every asset a scene shows. Scenes carry one `visual_source`; the recap
    carries a `visual_sources` list, and this is what reads both."""
    if scene.get("visual_sources"):
        return list(scene["visual_sources"])
    source = scene.get("visual_source")
    return [source] if source else []


def unused_assets(profile: PetProfile, script: dict) -> list[MediaAsset]:
    """Profile assets no scene references, in profile order.

    Matches on asset_id or filename because that's what the script prompt
    offers the model, and pipeline/rendering.py resolves either."""
    used: set[str] = set()
    for scene in script.get("scenes", []):
        used.update(scene_sources(scene))

    return [
        asset
        for asset in profile.media.assets
        if asset.asset_id not in used and asset.url.rsplit("/", 1)[-1] not in used
    ]


def recap_asset_duration(asset_count: int) -> float:
    """How long each leftover asset gets on screen.

    Shrinks as the leftovers pile up so the recap stays a recap, with a floor
    below which a photo is no longer readable — past that the recap simply
    runs longer, because dropping assets is the one thing it exists to avoid.
    """
    if asset_count <= 0:
        return 0.0
    fair_share = config.RECAP_MAX_DURATION / asset_count
    return max(config.RECAP_MIN_ASSET_DURATION, min(config.RECAP_ASSET_DURATION, fair_share))


def append_recap_scene(script: dict, profile: PetProfile) -> dict:
    """Return a copy of the script with a recap shot appended.

    Returns the script unchanged when every asset is already on screen. The
    copy is deliberate: the script recorded on the job row is the one that was
    rendered, but callers that kept a reference to the original (the three
    styles written to storage/output/<pet_id>/scripts/) shouldn't see it grow
    a scene they didn't ask for.
    """
    leftovers = unused_assets(profile, script)
    if not leftovers:
        return script

    scenes = script.get("scenes", [])
    if not scenes:
        return script

    per_asset = recap_asset_duration(len(leftovers))
    start = scenes[-1]["end"]

    recap = {
        "scene_id": max(s["scene_id"] for s in scenes) + 1,
        "start": start,
        "end": round(start + per_asset * len(leftovers), 2),
        "purpose": RECAP_PURPOSE,
        "visual_sources": [a.asset_id for a in leftovers],
        # No narration: the story has already finished, and the CTA line is
        # what should be readable while the photos flick past.
        "narration": "",
        "subtitle": script.get("cta", ""),
    }

    updated = dict(script)
    updated["scenes"] = [*scenes, recap]
    updated["duration"] = recap["end"]
    return updated

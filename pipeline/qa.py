from __future__ import annotations

from pipeline import config
from pipeline.background import BackgroundMode
from pipeline.overlay_renderer import REQUIRED_FIELDS, OverlayTemplate
from pipeline.props import prop_specs_from_job


def validate_script_structure(script: dict, prop_specs: dict | None = None) -> list[str]:
    """Lightweight structural QA stand-in (docs/architecture.md §11 影音品質
    檢查, not the full weighted-score QA Agent): catches format violations
    that would silently break the editing pipeline (gaps/overlaps make the
    concatenated video shorter or longer than the declared duration) or the
    platform-compliance requirement that subtitles always be present —
    independent of narrative content quality, which this does not judge.

    prop_specs is the run's per-scene props (pipeline/props.py). Passed in
    rather than read off the script, because a prop is a reviewer's decision
    about one photograph and lives on the job row, not in the script — but it
    still has to be counted here, since "how much of this video is real" is a
    question about the whole film.
    """
    issues: list[str] = []
    scenes = script.get("scenes", [])
    declared_duration = script.get("duration")

    scene_count = len(scenes)
    if not (config.MIN_SCENES <= scene_count <= config.MAX_SCENES):
        issues.append(f"scene count {scene_count} outside {config.MIN_SCENES}-{config.MAX_SCENES}")

    expected_start = 0
    for scene in scenes:
        scene_id = scene.get("scene_id")
        start, end = scene.get("start"), scene.get("end")

        if start != expected_start:
            issues.append(
                f"scene {scene_id} starts at {start}, expected {expected_start} "
                "(gap or overlap with previous scene)"
            )
        expected_start = end

        if not (scene.get("subtitle") or "").strip():
            issues.append(f"scene {scene_id} has an empty subtitle")

        issues.extend(_background_issues(scene, scene_id))
        issues.extend(_overlay_issues(scene, scene_id))

    if scenes and declared_duration is not None and expected_start != declared_duration:
        issues.append(
            f"scenes total {expected_start}s, does not match declared duration {declared_duration}s"
        )

    repeated = _repeated_background_prompts(scenes)
    if repeated:
        # Not a defect in the file, a defect in the story: the whole reason
        # backgrounds moved into the script was so a video could travel from
        # one place to another. The same sentence on every shot is the old
        # single-prompt behaviour wearing the new schema.
        issues.append(
            "scenes " + ", ".join(str(i) for i in repeated) + " share one background description "
            "— the setting does not move through the story"
        )

    crowded = _overloaded_overlays(scenes)
    if crowded:
        # A panel earns its place by being the exception. On every shot it is
        # not a layout, it is a wall of text over the animal the viewer came
        # to see — and the subtitle is already carrying the message.
        issues.append(
            "scenes " + ", ".join(str(i) for i in crowded) + " all carry an overlay panel "
            "— the panels stop reading as emphasis and start covering the pet"
        )

    dressed = _prop_scene_ids(scenes, prop_specs)
    if scenes and len(dressed) > len(scenes) * config.PROPS_MAX_SCENE_RATIO:
        # An adoption video exists to show an adopter this animal. Every shot
        # wearing something it does not own stops being a record of the pet
        # and becomes a costume shoot — and the adopter is deciding, from
        # these frames, what they would be taking home.
        issues.append(
            "scenes "
            + ", ".join(str(i) for i in dressed)
            + " carry generated props — over "
            + f"{config.PROPS_MAX_SCENE_RATIO:.0%} of the video, so an adopter would be judging "
            "this animal largely on things it does not actually have"
        )

    if scenes and all(_replaces_its_setting(scene) for scene in scenes):
        # A shelter's video is meant to show an adopter this animal, in some
        # place it has actually been. Every shot invented is a different
        # product, and not one this pipeline should hand over quietly.
        issues.append("every scene replaces its setting — no shot shows the pet where it really is")

    return issues


def _repeated_background_prompts(scenes: list[dict]) -> list[int]:
    """scene_ids sharing a background description with another scene."""
    seen: dict[str, list[int]] = {}
    for scene in scenes:
        block = scene.get("background")
        if not isinstance(block, dict):
            continue
        prompt = (block.get("prompt") or "").strip().lower()
        if prompt:
            seen.setdefault(prompt, []).append(scene.get("scene_id"))
    return sorted(scene_id for ids in seen.values() if len(ids) > 1 for scene_id in ids)


def _replaces_its_setting(scene: dict) -> bool:
    block = scene.get("background")
    return isinstance(block, dict) and block.get("mode") == BackgroundMode.REPLACE.value


def _background_issues(scene: dict, scene_id) -> list[str]:
    """A scene's background block, checked for the mistakes that make it do
    nothing rather than what was asked.

    Neither is fatal — pipeline/background.py falls back to showing the
    photograph — which is exactly why they have to be reported: a script
    that asked for a park and silently got a bedroom is worse than one that
    said so.
    """
    block = scene.get("background")
    if block is None:
        return []
    if not isinstance(block, dict):
        return [f"scene {scene_id} has a background that is not an object"]

    issues = []
    mode = block.get("mode")
    if mode == BackgroundMode.REPLACE.value and not config.BACKGROUND_ALLOW_SCRIPT_REPLACE:
        issues.append(
            f"scene {scene_id} asked to replace its setting, which only a reviewer who has "
            "seen the photo may choose — it was extended instead"
        )
    if mode not in {m.value for m in BackgroundMode}:
        issues.append(
            f"scene {scene_id} asks for unknown background mode {mode!r} "
            f"(expected one of {sorted(m.value for m in BackgroundMode)})"
        )
    elif mode != BackgroundMode.KEEP.value and not (block.get("prompt") or "").strip():
        issues.append(f"scene {scene_id} asks for a {mode} background but describes nothing")

    return issues


def _overloaded_overlays(scenes: list[dict]) -> list[int]:
    """scene_ids carrying a panel, when nearly every shot carries one.

    Reported at the film level rather than per scene because no single panel
    is the mistake: the ratio is.
    """
    with_panel = [
        scene.get("scene_id")
        for scene in scenes
        if isinstance(scene.get("overlay"), dict)
        and scene["overlay"].get("template") not in (None, OverlayTemplate.NONE.value)
    ]
    if (
        len(scenes) >= config.MIN_SCENES
        and len(with_panel) > len(scenes) * config.OVERLAY_MAX_SHARE
    ):
        return with_panel
    return []


def _overlay_issues(scene: dict, scene_id) -> list[str]:
    """A scene's overlay block, checked for the mistakes that make it render
    nothing rather than what was asked.

    Same contract as _background_issues: none of these is fatal — the shot is
    simply made without a panel — which is precisely why they have to be
    reported. A script that asked for an information sidebar and silently got
    a bare frame is worse than one that said so.
    """
    block = scene.get("overlay")
    if block is None:
        return []
    if not isinstance(block, dict):
        return [f"scene {scene_id} has an overlay that is not an object"]

    template = block.get("template")
    valid = {t.value for t in OverlayTemplate}
    if template not in valid:
        return [
            (
                f"scene {scene_id} asks for unknown overlay template {template!r} "
                f"(expected one of {sorted(valid)})"
            )
        ]
    if template == OverlayTemplate.NONE.value:
        return []

    required = REQUIRED_FIELDS[OverlayTemplate(template)]
    for field in required:
        value = block.get(field)
        if isinstance(value, list):
            if any(str(item).strip() for item in value):
                return []
        elif str(value or "").strip():
            return []
    return [
        (
            f"scene {scene_id} asks for a {template} overlay but leaves "
            f"{' / '.join(required)} empty — the panel was dropped"
        )
    ]


def _prop_scene_ids(scenes: list[dict], prop_specs: dict | None) -> list[int]:
    """scene_ids wearing at least one generated prop, from either source.

    Two sources because there are two: the reviewer's own placements, stored
    on the job row, and a script's `props` block for the rare setup where
    config.PROPS_ALLOW_SCRIPT is on. Counting only one of them would let the
    other slip past the ratio.
    """
    from_job = set(prop_specs_from_job({"prop_specs": prop_specs}))
    from_script = {
        scene.get("scene_id")
        for scene in scenes
        if isinstance(scene.get("props"), list) and scene["props"]
    }
    known = {scene.get("scene_id") for scene in scenes}
    return sorted((from_job | from_script) & known)

from __future__ import annotations

from pipeline import config


def validate_script_structure(script: dict) -> list[str]:
    """Lightweight structural QA stand-in (docs/architecture.md §11 影音品質
    檢查, not the full weighted-score QA Agent): catches format violations
    that would silently break the editing pipeline (gaps/overlaps make the
    concatenated video shorter or longer than the declared duration) or the
    platform-compliance requirement that subtitles always be present —
    independent of narrative content quality, which this does not judge.
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

    if scenes and declared_duration is not None and expected_start != declared_duration:
        issues.append(
            f"scenes total {expected_start}s, does not match declared duration {declared_duration}s"
        )

    return issues

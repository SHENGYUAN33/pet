from __future__ import annotations

import re

from pipeline import config
from pipeline.background import BackgroundMode
from pipeline.profile import PetProfile


def find_missing_disclosures(script: dict, profile: PetProfile) -> list[str]:
    """Interim stand-in for the Fact-check Agent (docs/architecture.md §4,
    §8, not built yet): every required care restriction must appear
    somewhere in the generated script's narration/subtitle text.

    This is a plain substring match, not semantic understanding. Verified
    against a live-LLM run with two restrictions: it correctly caught a
    style that dropped a short phrase restriction ("不與其他貓咪同住")
    entirely, but false-positived on a full-sentence restriction
    ("需要長期服用腎臟處方飼料，不可中斷") that another style disclosed
    with different wording ("需長期服用腎臟處方飼料"). Short, keyword-style
    restrictions are reliably caught; full-sentence restrictions are prone
    to false positives once the model paraphrases them.

    Operational guidance: keep Profile `restrictions` entries short and
    keyword-like (as the appeal/lifestyle_fit/care_needs tags already are)
    rather than full sentences, so this stays reliable. Treat a clean
    result as "nothing obviously missing" and a flagged result as "review
    this, it might be a false positive" — not a verified pass/fail. Real
    semantic fact-checking (matching against the full Profile, not just
    restrictions) is still an MVP-stage TODO (the actual Fact-check Agent).
    """
    script_text = " ".join(
        f"{scene.get('narration', '')} {scene.get('subtitle', '')}"
        for scene in script.get("scenes", [])
    )
    return [
        restriction
        for restriction in profile.personality_tags.restrictions
        if restriction not in script_text
    ]


def find_background_risks(script: dict) -> list[str]:
    """Generated settings that would say something about the pet that isn't
    in its Profile.

    A replaced background is invented, and the reviewer has agreed to that —
    but "invented" is not the same as "free". A living room with a child in
    it claims this animal is good with children; a clinic claims something
    about its health. Neither was promised and neither is in the Profile,
    which is the only thing allowed to say what is true of this pet
    (CLAUDE.md: Pet Profile 是唯一事實來源).

    The same list catches the other way a background goes wrong: a prompt
    that names an animal makes the model paint one, so the finished shot has
    a second cat standing next to the real one.

    Only REPLACE is checked. EXTEND continues the photograph the camera
    took — whatever is in it was already there, and it is not this
    function's business to object to it.

    Like find_missing_disclosures, this is word matching rather than
    understanding: a clean result means "nothing obviously wrong", not a
    verified pass.
    """
    risks: list[str] = []
    for scene in script.get("scenes", []):
        block = scene.get("background")
        if not isinstance(block, dict) or block.get("mode") != BackgroundMode.REPLACE.value:
            continue

        prompt = block.get("prompt") or ""
        found = sorted(
            {
                term
                for term in config.BACKGROUND_FORBIDDEN_TERMS
                if re.search(rf"\b{re.escape(term)}\b", prompt, re.IGNORECASE)
            }
        )
        if found:
            risks.append(
                f"scene {scene.get('scene_id')}: generated background mentions "
                f"{', '.join(found)} — it would imply something about this pet "
                f"that its Profile does not say"
            )
    return risks

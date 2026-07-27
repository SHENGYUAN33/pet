from __future__ import annotations

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

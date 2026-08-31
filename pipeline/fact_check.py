from __future__ import annotations

import json
import re

from pipeline import config
from pipeline.background import BackgroundMode
from pipeline.profile import PetProfile
from providers.base import LLMProvider

_DISCLOSURE_PROMPT = """\
下面是一支寵物領養短影音的旁白與字幕，以及這隻寵物在資料庫裡「必須揭露」的照護限制。

請判斷每一條限制，是否有在影片文字裡被說出來——**用不同的說法表達也算數**，
只要看完影片的人會知道這件事。文字裡完全沒有提到、或只提到相似但不同的事情，才算沒說到。

只輸出一個 JSON 物件，不要有其他文字、不要用 markdown code block：
{{"disclosed": ["<有被說到的限制，照原文抄回來>"], "missing": ["<沒被說到的限制，照原文抄回來>"]}}

必須揭露的限制：
{restrictions}

影片文字：
{script_text}
"""


def _script_text(script: dict) -> str:
    return " ".join(
        f"{scene.get('narration', '')} {scene.get('subtitle', '')}"
        for scene in script.get("scenes", [])
    ).strip()


def _extract_json(raw: str) -> dict:
    """These models wrap JSON in prose or code fences whatever the prompt
    says — same leniency as pipeline/script_gen.py's."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"LLM response did not contain a JSON object: {raw!r}")
    return json.loads(match.group(0), strict=False)


def find_missing_disclosures(
    script: dict, profile: PetProfile, llm: LLMProvider | None = None
) -> list[str]:
    """Required care restrictions that the script never tells the viewer.

    Two stages, and the order matters. A restriction whose exact wording
    appears in the script is disclosed — that is certain, needs no model, and
    covers most of them. Only the ones the substring check *cannot* find go
    to the LLM, because that check has exactly one failure mode: it says
    "missing" when the script disclosed the same thing in different words.
    Measured against a live run, it correctly caught a style that dropped
    "不與其他貓咪同住" entirely, and falsely accused another that had
    disclosed "需要長期服用腎臟處方飼料，不可中斷" as "需長期服用腎臟處方飼料".

    So the model is only ever asked to *clear* a restriction, never to
    condemn one. If it is unavailable or answers with nonsense, the
    substring verdict stands: this reports something to review rather than
    something to trust, and the cautious answer is the one that asks a human
    to look.

    llm=None keeps the old behaviour exactly, for callers and tests that have
    no model to hand.
    """
    text = _script_text(script)
    candidates = [
        restriction
        for restriction in profile.personality_tags.restrictions
        if restriction not in text
    ]
    if not candidates or llm is None or not config.FACT_CHECK_SEMANTIC_ENABLED:
        return candidates

    try:
        answer = _extract_json(
            llm.complete(
                _DISCLOSURE_PROMPT.format(
                    restrictions="\n".join(f"- {c}" for c in candidates),
                    script_text=text,
                )
            )
        )
        cleared = {str(item).strip() for item in answer.get("disclosed") or []}
    except Exception:  # noqa: BLE001 - boundary: an external model and its output
        return candidates

    return [c for c in candidates if c not in cleared]


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


_CLAIM_PROMPT = """\
下面是一支寵物領養短影音的旁白與字幕，以及這隻寵物在資料庫裡的**全部**資料。

領養影片不可以說出資料裡沒有的事。請找出影片文字裡「**對這隻寵物提出、但資料裡查不到根據**」的說法，
例如資料沒提到卻說牠親近小孩、會某項才藝、有某段身世、或健康狀況。

不算問題的：情緒或語氣的表達（例如「快來抱我」「我好想有個家」）、
資料裡有寫的事情換句話說、以及對領養人的呼籲。

只輸出一個 JSON 物件，不要有其他文字、不要用 markdown code block：
{{"unsupported": [{{"quote": "<影片裡的原句>", "why": "<資料裡查不到什麼>"}}]}}
沒有問題就回 {{"unsupported": []}}。

寵物資料：
{profile_json}

影片文字：
{script_text}
"""


def find_unsupported_claims(
    script: dict, profile: PetProfile, llm: LLMProvider | None = None
) -> list[str]:
    """Things the script says about this pet that its Profile does not support.

    The other half of fact-checking, and the half that had no check at all:
    find_missing_disclosures asks whether something required was left out,
    this asks whether something was made up. An adoption video that invents
    "我最愛跟小孩玩" is how an adopter takes home an animal that was never
    described to them, and it is the failure the Profile-as-single-source-of-
    truth rule exists to prevent.

    Substring matching cannot do this at all — a fabricated sentence looks
    exactly like a true one — so unlike the disclosure check there is no
    fast path. Without a model it returns nothing rather than guessing, and
    says so by simply having nothing to report.

    Like every check here it reports; the reviewer decides. A 7B model
    reading a Profile will sometimes call a fair paraphrase a fabrication.
    """
    if llm is None or not config.FACT_CHECK_SEMANTIC_ENABLED:
        return []

    text = _script_text(script)
    if not text:
        return []

    try:
        answer = _extract_json(
            llm.complete(
                _CLAIM_PROMPT.format(
                    profile_json=profile.model_dump_json(indent=2),
                    script_text=text,
                )
            )
        )
    except Exception:  # noqa: BLE001 - boundary: an external model and its output
        return []

    claims = []
    for item in answer.get("unsupported") or []:
        if isinstance(item, dict):
            quote = str(item.get("quote") or "").strip()
            why = str(item.get("why") or "").strip()
        else:
            quote, why = str(item).strip(), ""
        if quote:
            claims.append(f"「{quote}」{('：' + why) if why else ''} — 資料裡查不到根據，請確認")
    return claims

from __future__ import annotations

import json
import re

from pipeline.profile import PetProfile
from providers.base import LLMProvider

# Matches the three narrative styles from docs/architecture.md §2
SCRIPT_STYLES = ["cute", "warm_story", "contrast_humor"]

_STYLE_DESCRIPTIONS = {
    "cute": "萌系自我介紹：適合活潑、幼齡、外觀討喜的寵物，語氣可愛俏皮",
    "warm_story": "溫暖故事型：適合成犬/成貓或曾有特殊經歷的寵物，語氣溫暖真摯",
    "contrast_humor": "反差幽默型：適合個性鮮明、表情豐富的寵物，語氣帶點自嘲幽默",
}

_PROMPT_TEMPLATE = """\
你是寵物領養短影音的腳本編劇。請根據下面的寵物資料，用寵物的第一人稱視角，
寫一支 {duration} 秒的領養廣告腳本，風格是「{style_desc}」。

只能輸出一個 JSON 物件，不要加任何說明文字、不要用 markdown code block，格式如下：
{{
  "pet_id": "{pet_id}",
  "title": "...",
  "style": "{style}",
  "duration": {duration},
  "language": "zh-TW",
  "scenes": [
    {{"scene_id": 1, "start": 0, "end": 3, "purpose": "hook", "visual_source": "<必須是下方素材清單中的檔名>", "narration": "...", "subtitle": "..."}}
  ],
  "cta": "...",
  "cta_url": "{contact_url}"
}}

嚴格規則：
1. scenes 陣列切分整支影片時長，時間軸須從 0 開始、連續不重疊、加總等於 {duration}
2. visual_source 必須從下方「可用素材清單」中選擇實際檔名，不可捏造不存在的素材
3. 不可捏造下方資料以外的健康狀況、技能或救援故事
4. 必要照護限制（見下方「必要揭露」）必須至少出現在一個 scene 的 narration 或 subtitle 中
5. 每句 narration 不超過 22 個中文字
6. 開場（第一個 scene）必須是 hook，出現寵物動作與吸睛句

可用素材清單：
{asset_list}

必要揭露（不可省略）：
{restrictions}

寵物資料：
{profile_json}
"""


def _build_prompt(profile: PetProfile, *, style: str, duration: int) -> str:
    asset_list = "\n".join(
        f"- {a.asset_id} ({a.type}): {a.url.rsplit('/', 1)[-1]}"
        for a in profile.media.assets
    ) or "(無可用素材，請只使用 visual_source: \"generated\" 並標示為 AI 生成)"

    restrictions = "、".join(profile.personality_tags.restrictions) or "（無）"

    return _PROMPT_TEMPLATE.format(
        duration=duration,
        style=style,
        style_desc=_STYLE_DESCRIPTIONS[style],
        pet_id=profile.pet_id,
        contact_url=profile.contact_url,
        asset_list=asset_list,
        restrictions=restrictions,
        profile_json=profile.model_dump_json(indent=2),
    )


def _extract_json(raw: str) -> dict:
    """Ollama models sometimes wrap JSON in prose or code fences; pull out
    the first {...} block before parsing."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"LLM response did not contain a JSON object: {raw!r}")
    return json.loads(match.group(0))


def generate_script(
    profile: PetProfile, llm: LLMProvider, *, style: str = "cute", duration: int = 30
) -> dict:
    if style not in SCRIPT_STYLES:
        raise ValueError(f"Unknown style {style!r}, expected one of {SCRIPT_STYLES}")
    prompt = _build_prompt(profile, style=style, duration=duration)
    raw = llm.complete(prompt)
    return _extract_json(raw)


def generate_all_styles(
    profile: PetProfile, llm: LLMProvider, *, duration: int = 30
) -> dict[str, dict]:
    """Generate all three narrative styles for human review (see
    docs/architecture.md §2 — never publish a single auto-picked version)."""
    return {
        style: generate_script(profile, llm, style=style, duration=duration)
        for style in SCRIPT_STYLES
    }

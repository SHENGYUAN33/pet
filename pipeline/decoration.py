"""The layout a shot is dressed in: border, vignette, and the pet's details.

docs/architecture.md §4 lists 字幕/貼圖/特效 as part of the editing stage, and
only the subtitle and the AI-generation disclosure were ever built. Everything
else about how the video *looks* was whatever the phone camera happened to
produce.

This is the part of "make it look made rather than filmed" that needs no
generated pixels at all. It is plain FFmpeg compositing, so unlike a replaced
background it is deterministic, cannot alter the animal, needs no disclosure,
and produces the same result on the same input every time. After background
replacement turned out to depend entirely on how well one photo segments,
that difference is the point.

What it is *for* is not decoration. A shelter video is watched on mute, in a
feed, for about three seconds. The name and age of the animal being on screen
immediately, and the frame reading as deliberate rather than accidental, are
the things that decide whether the next three seconds happen.

Which look a video gets is keyed to the script's own `style` — the creative
decision is already recorded there (CLAUDE.md: 畫面上的創作決定要寫在腳本裡),
and what each style *looks* like is a design system, which belongs in config
rather than in something a 7B model invents per run.
"""

from __future__ import annotations

from pipeline import config
from pipeline.profile import PetProfile


def palette_for(style: str) -> dict[str, str]:
    """The accent colours this narrative style is dressed in.

    Falls back to the default palette rather than raising: an unknown style
    is a reason to look ordinary, not a reason to lose the video.
    """
    return config.DECOR_PALETTES.get(style, config.DECOR_PALETTES[config.DECOR_DEFAULT_STYLE])


def identity_line(profile: PetProfile) -> str:
    """The one line that has to be readable in the first three seconds.

    Name, age, sex, breed — what someone scrolling needs before they decide
    whether this animal is one they could take. Fields the profile leaves
    empty are simply left out rather than shown as blanks.
    """
    sex = config.DECOR_SEX_LABELS.get(profile.sex.strip().lower(), profile.sex)
    parts = [profile.name, profile.age, sex, profile.breed]
    return " · ".join(part.strip() for part in parts if part and part.strip())


def vignette_filter() -> str:
    """Darken the corners so the eye goes to the animal.

    A fixed, gentle amount: strong enough to shape the frame, weak enough
    that nobody can tell it is there.
    """
    return f"vignette=angle={config.DECOR_VIGNETTE_ANGLE}"


def border_filter(colour: str, width: int, height: int) -> str:
    """An inset frame in the style's accent colour.

    Drawn inside the picture rather than added around it, so every shot
    stays exactly the delivery size and the concatenation still
    stream-copies. Four boxes rather than one thick outline because
    drawbox's own thickness grows inward from a rectangle that would have to
    be positioned anyway.
    """
    inset = config.DECOR_BORDER_INSET
    thickness = config.DECOR_BORDER_WIDTH
    box_width = width - inset * 2
    box_height = height - inset * 2
    return (
        f"drawbox=x={inset}:y={inset}:w={box_width}:h={box_height}"
        f":color={colour}@{config.DECOR_BORDER_OPACITY}:t={thickness}"
    )

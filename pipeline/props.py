"""Painting a small object onto the pet: a collar, a toy beside its paws.

The counterpart of pipeline/background.py, and deliberately not part of it,
because it breaks the guarantee that module is built around. Every background
treatment ends by compositing the animal's real pixels back, so "the pet is
never repainted" holds absolutely. A collar cannot honour that — the band has
to sit *on* the neck — so this is the one edit in the project that is allowed
to alter the animal, and it pays for that three ways:

    * it always carries the AI-generation disclosure, like REPLACE
      (docs/architecture.md §5 strategy C);
    * a script may never choose it (config.PROPS_ALLOW_SCRIPT), because a
      model that cannot see the photograph cannot judge whether the animal is
      in a pose where this works;
    * the region is named by the person looking at the photo.

That last one is the interesting design decision. The obvious approach is to
take the subject's bounding box and call the top 15-30% of it "the neck". That
only holds for an upright, head-up animal; shelter photos are as often taken
from directly above, or of a cat lying on its back, and on those the band
lands on the floor. SAM3 returns one silhouette with no anatomy in it, so
there is nothing in the mask to ask either. Since this is a human-only feature
already, the honest answer is to let the human who is looking at the picture
say where — which is also what the project concluded when background REPLACE
was demoted to reviewer-only.

What is deliberately absent: a human hand reaching in to touch the animal. A
hand in frame says this pet tolerates being handled by a stranger, which the
Profile never said — config.BACKGROUND_FORBIDDEN_TERMS bans exactly those
words from a generated setting for that reason, and generating one here would
be a way around pipeline/fact_check.py rather than a feature.
"""

from __future__ import annotations

import enum
import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from pipeline import config
from providers.base import ImageEditingProvider


class PropPlacement(str, enum.Enum):
    """What kind of object is being painted, and how it meets the animal.

    str-valued like BackgroundMode, so it round-trips through the job row and
    a JSON response without conversion.
    """

    #: Something worn: a collar, a bandana. Painted only where the reviewer's
    #: region and the animal overlap, so it cannot spill onto the floor.
    COLLAR = "collar"
    #: Something placed: a plush toy by the paws. Painted only where the
    #: region does *not* overlap the animal, so it sits beside it rather than
    #: through it.
    TOY = "toy"

    @property
    def on_subject(self) -> bool:
        """Whether the paint lands on the animal or beside it."""
        return self is PropPlacement.COLLAR

    @property
    def label(self) -> str:
        """What to call this in progress messages a reviewer reads."""
        return {PropPlacement.COLLAR: "配戴道具", PropPlacement.TOY: "身旁道具"}[self]

    @property
    def default_prompt(self) -> str:
        return {
            PropPlacement.COLLAR: config.PROPS_COLLAR_PROMPT,
            PropPlacement.TOY: config.PROPS_TOY_PROMPT,
        }[self]

    @property
    def default_size(self) -> tuple[float, float]:
        """Width and height of the painted region, as fractions of the frame.

        A collar is a band across the body; a toy is a blob beside it, so the
        two are not the same shape and one default would suit neither.
        """
        return {
            PropPlacement.COLLAR: (config.PROPS_COLLAR_WIDTH, config.PROPS_COLLAR_HEIGHT),
            PropPlacement.TOY: (config.PROPS_TOY_WIDTH, config.PROPS_TOY_HEIGHT),
        }[self]


class SceneProp(BaseModel):
    """One prop, and where on the picture it goes.

    region is (left, top, right, bottom) in fractions of the image — the box
    the reviewer drew. Fractions rather than pixels so the same spec survives
    the photo being rescaled anywhere later in the pipeline.
    """

    placement: PropPlacement
    region: tuple[float, float, float, float]
    #: None means the placement's own default wording.
    prompt: str | None = None

    @field_validator("prompt")
    @classmethod
    def _no_people_or_other_animals(cls, value):
        """Enforced on the model, not only at the API boundary.

        A rule that lives in one endpoint is a rule the CLI, a resumed run and
        a script-supplied prop all walk straight past. Putting it here means
        nothing anywhere can construct a prop that asks for a human hand on a
        real animal — which is the whole point of the check
        (CLAUDE.md: 規則寫在 repository 層).
        """
        found = forbidden_terms_in(value)
        if found:
            raise ValueError(
                "prop prompt may not mention " + ", ".join(found) + " — a person touching the "
                "animal, or a second animal beside it, claims something this pet's Profile "
                "never said"
            )
        return value

    @field_validator("region")
    @classmethod
    def _ordered_and_on_canvas(cls, value):
        left, top, right, bottom = value
        for part in value:
            if not 0.0 <= part <= 1.0:
                raise ValueError(f"region fractions must be within 0..1, got {value}")
        if left >= right or top >= bottom:
            raise ValueError(f"region must have positive width and height, got {value}")
        return value


def region_around(
    anchor: tuple[float, float], placement: PropPlacement, size: tuple[float, float] | None = None
) -> tuple[float, float, float, float]:
    """The box for a prop centred on one point the reviewer clicked.

    A convenience for callers that have a point rather than a rectangle — a
    click is a far easier thing to ask for than a drag. Clamped to the canvas
    rather than allowed to run off it: a point near the edge should give a
    smaller region, not an invalid one.
    """
    width, height = size or placement.default_size
    x, y = anchor
    left = max(0.0, min(1.0, x - width / 2))
    right = max(0.0, min(1.0, x + width / 2))
    top = max(0.0, min(1.0, y - height / 2))
    bottom = max(0.0, min(1.0, y + height / 2))
    # A click hard against an edge can collapse one side; nudge it back to a
    # usable box rather than raising at the far end of a slow pipeline.
    if left >= right:
        left, right = max(0.0, right - width), min(1.0, left + width)
    if top >= bottom:
        top, bottom = max(0.0, bottom - height), min(1.0, top + height)
    return left, top, right, bottom


class SceneProps(BaseModel):
    """The props one shot is wearing. A list, because a collar and a toy are
    two separate paint passes and a shot may want both."""

    props: list[SceneProp] = Field(default_factory=list)


def resolve_scene_props(
    scene: dict,
    *,
    override: list[SceneProp] | None = None,
) -> list[SceneProp]:
    """The props this shot gets.

    The reviewer's own list wins outright, the same way a named background
    scene wins over the script's choice. A script's own `props` block is read
    only when config.PROPS_ALLOW_SCRIPT is on, and it is off by default: a
    model that cannot see the photograph cannot know whether the animal is
    lying on its back, and a collar painted across a belly is worse than no
    collar.
    """
    if override:
        return list(override)

    if not config.PROPS_ALLOW_SCRIPT:
        return []

    block = scene.get("props")
    if not isinstance(block, list):
        return []

    resolved: list[SceneProp] = []
    for item in block:
        if not isinstance(item, dict):
            continue
        try:
            resolved.append(SceneProp.model_validate(item))
        except ValidationError:
            # An unusable entry costs the prop, never the shot — the same
            # contract resolve_scene_background() keeps, and pipeline/qa.py
            # is what tells the reviewer.
            continue
    return resolved


def apply_props(
    image_path: str,
    provider: ImageEditingProvider,
    *,
    props: list[SceneProp],
    output_path: str,
    subject: str | None = None,
) -> str:
    """Paint every prop onto the photo in turn, returning the path to use.

    Sequential rather than one pass with a combined mask: two props are two
    different objects with two different descriptions, and a single prompt
    asking for "a collar and a toy" gives the sampler licence to put either
    anywhere in the union of the regions.

    Each pass reads the previous pass's output, so a collar is still there
    when the toy is painted. With no props the original path comes straight
    back — callers use the returned path and must not assume a new file
    exists, exactly as with apply_background().
    """
    if not props:
        return image_path

    current = image_path
    for index, prop in enumerate(props):
        # Numbered so a two-prop shot leaves both intermediates on disk: when
        # one of them comes out wrong, which pass produced it is the first
        # thing anyone needs to know.
        step_output = output_path if index == len(props) - 1 else f"{output_path}.{index}.png"
        current = provider.add_prop(
            current,
            region=prop.region,
            on_subject=prop.placement.on_subject,
            prompt=prop.prompt or prop.placement.default_prompt,
            output_path=step_output,
            subject=subject,
        )
    return current


def prop_specs_from_job(job: dict) -> dict[int, list[SceneProp]]:
    """Read a run's prop settings back off its job row.

    The round trip matters more here than for most settings: resuming has to
    finish the video that was being made, and a shot that quietly lost its
    collar halfway through is a different video (CLAUDE.md: 續跑必須把同一支
    影片做完). Keys come back as strings because JSON object keys always are.

    An unreadable entry is dropped rather than raised on: a resume that
    refuses to start because one stored region is malformed strands the whole
    run, and the shot without its prop is still the shot.
    """
    stored = job.get("prop_specs") or {}
    if not isinstance(stored, dict):
        return {}

    resolved: dict[int, list[SceneProp]] = {}
    for raw_scene_id, raw_props in stored.items():
        try:
            scene_id = int(raw_scene_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_props, list):
            continue
        props = []
        for item in raw_props:
            if not isinstance(item, dict):
                continue
            try:
                props.append(SceneProp.model_validate(item))
            except ValidationError:
                continue
        if props:
            resolved[scene_id] = props
    return resolved


def forbidden_terms_in(prompt: str | None) -> list[str]:
    """Words in a prop description that must never reach the sampler.

    Two different failures, one list (config.PROPS_FORBIDDEN_TERMS):

    A person. "a hand gently holding the cat" puts a stranger in physical
    contact with a real adoptable animal — a claim its Profile never made,
    and the prop mask makes it *more* convincing rather than less. This is
    the same rule pipeline/fact_check.py enforces on generated backgrounds,
    and a prop prompt is the one place it could otherwise be walked around.

    Another animal. Naming one makes the model paint one, and the toy region
    sits beside the pet — precisely where a second cat would appear.

    Whole-word matching, so "cat" does not fire on "delicate" and "arm" does
    not fire on "warm"; warm lighting is in the shipped defaults, so that one
    is not hypothetical. Returns the offending words, sorted, so a caller can
    say which ones rather than only that something was wrong.
    """
    if not prompt:
        return []
    return sorted(
        {
            term
            for term in config.PROPS_FORBIDDEN_TERMS
            if re.search(rf"\b{re.escape(term)}\b", prompt, re.IGNORECASE)
        }
    )


def prop_specs_to_job(prop_specs: dict[int, list[SceneProp]] | None) -> dict:
    """The job-row form of a run's props: the inverse of prop_specs_from_job.

    Its own function rather than a dict comprehension at each call site,
    because there are three of them (the job row, QA's count, and a
    revision's inherited set) and they must not be able to disagree about
    what this run is painting. Keys are strings because JSON object keys
    always are.
    """
    return {
        str(scene_id): [prop.model_dump(mode="json") for prop in props]
        for scene_id, props in (prop_specs or {}).items()
    }

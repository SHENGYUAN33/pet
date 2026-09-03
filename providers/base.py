"""Runtime Provider Adapter interfaces.

Concrete design rationale lives in docs/reference/provider_adapter.py and
docs/architecture.md §7. This module is the actual code callers import —
never call a vendor SDK directly from pipeline/ code, always go through
one of these interfaces so a provider can be swapped (commercial <->
open-source) without touching call sites.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return raw text completion for the given prompt."""


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, *, voice_profile: str, language: str, output_path: str) -> str:
        """Synthesize narration audio to output_path, return the path."""


class VideoGenerationProvider(ABC):
    """Strategy B (docs/architecture.md §5): animate a still photo when a
    scene lacks real footage. Output is a short raw clip at the model's
    native fps/duration — pipeline/i2v.py hands it to the same loop/crop/
    constant-fps normalization real video clips already go through, so this
    interface only needs to produce *a* clip, not one of the exact target
    duration."""

    def preflight(self) -> None:
        """Raise if this provider clearly cannot run right now.

        Callers use it to fail before the expensive work leading up to the
        first animate_image call. Only providers with a checkable external
        dependency need to override it (Wan talks to a ComfyUI server that
        has to be started by hand); the default is a no-op, so a provider
        that can't cheaply tell in advance simply reports at call time.

        This is a fast check, not a guarantee: the service can still go away
        between here and the call, which is why animate_image keeps its own
        error handling."""

    @abstractmethod
    def animate_image(
        self,
        image_path: str,
        *,
        duration_seconds: float,
        output_path: str,
        prompt: str | None = None,
    ) -> str:
        """Animate a still image into a short video clip at output_path,
        return the path. prompt is optional per-call motion guidance (e.g.
        "貓輕輕搖尾巴、抬頭看鏡頭") — providers that aren't text-conditioned
        (SVD) ignore it; prompt-aware providers (CogVideoX, Wan) use it to
        steer subject motion instead of falling back to a fixed default."""


class ImageEditingProvider(ABC):
    """Generative editing of a still image, ahead of the video stages.

    Two operations, and the difference between them is a product decision,
    not a technical one:

    outpaint_to_frame keeps the photograph's real background and generates
    only the empty margin the delivery frame leaves around it — nothing the
    camera saw is replaced. replace_background generates a whole new setting
    behind the subject, so the animal is real but the place is invented, and
    a shot made that way has to be disclosed as partly AI-generated
    (docs/architecture.md §5 strategy C).

    What both must guarantee: the subject's own pixels come through
    untouched. Implementations that sample the whole canvas have to put the
    original pixels back before returning, or the identity-consistency
    requirement that rules out redrawing the pet is quietly broken.

    Output is an ordinary image file. The result goes on to the same Ken
    Burns / Image-to-Video path any other photo takes, so this interface
    needs to produce *an image*, not a finished shot.
    """

    def preflight(self, *, mode: str = "extend") -> None:
        """Raise if this provider clearly cannot run right now.

        Same contract as VideoGenerationProvider.preflight above: a cheap
        early check so callers fail before the expensive work leading up to
        the first call, with a no-op default for providers that can't
        usefully tell in advance.

        mode says which treatment is coming ("extend" or "replace"), because
        they need different things installed — checking for a matting model
        an extend-only run will never load would refuse work that would have
        succeeded.
        """

    @abstractmethod
    def outpaint_to_frame(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
    ) -> str:
        """Extend image_path to target_width x target_height by generating
        the margin around it, and write the result to output_path (returned).

        The source picture must appear whole and unaltered inside the result
        — this fills space, it does not reframe or repaint. prompt describes
        the surroundings to generate ("溫暖的客廳，午後陽光"); providers
        substitute their own default when it is None.

        A source already at the target aspect ratio has no margin to fill;
        implementations return it unchanged rather than spending a
        generation pass to produce a copy.
        """

    @abstractmethod
    def replace_background(
        self,
        image_path: str,
        *,
        target_width: int,
        target_height: int,
        prompt: str | None = None,
        output_path: str,
        subject: str | None = None,
    ) -> str:
        """Put the subject of image_path into a generated setting at
        target_width x target_height, and write the result to output_path
        (returned).

        The subject is segmented out and kept as photographed; everything
        else in the frame is generated from prompt ("a grey cat on green
        grass in a sunny park"). Unlike outpaint_to_frame this always has
        work to do — a photo already shaped like the frame still has its
        whole background replaced.

        prompt describes the setting to generate, and should *not* name an
        animal: the real one is already in the frame, and asking for one
        paints a second beside it.

        subject names what to keep ("cat", "dog"), for implementations that
        segment by description rather than by salience. The caller knows the
        pet's species and the provider does not, so it is passed in;
        implementations that don't need it ignore it.
        """

    def add_prop(
        self,
        image_path: str,
        *,
        region: tuple[float, float, float, float],
        on_subject: bool,
        prompt: str | None = None,
        output_path: str,
        subject: str | None = None,
    ) -> str:
        """Paint a small object into one named region of image_path, and
        write the result to output_path (returned).

        Concrete rather than abstract, and that is deliberate: this interface
        already has implementations, and turning a new capability into a
        required method breaks every one of them. A provider that cannot do
        this inherits a clear refusal instead (CLAUDE.md: Provider Adapter
        介面變更需保持向下相容).

        This is the one operation here that is *allowed* to alter the animal,
        and the only one. A collar has to sit on the neck, which means the
        band inside `region` is genuinely repainted rather than composited
        back — so a shot made this way carries the AI-generation disclosure,
        exactly like a replaced setting. Everything outside `region` still
        comes through untouched, and implementations must guarantee that.

        region is (left, top, right, bottom) in fractions of the image, named
        by the person looking at the photograph. It is not derived from the
        subject's bounding box: "the top fifth of the animal" is only the
        neck when the animal is upright and head-up, and shelter photos are
        as often taken from above, or of a cat lying on its back.

        on_subject says how region meets the animal. True intersects it with
        the subject's own silhouette, so a collar cannot spill onto the floor
        beside it; False subtracts the silhouette, so a toy is placed *beside*
        the animal rather than through it.

        subject names what to segment ("cat", "dog"), as in
        replace_background — the caller knows the species, the provider does
        not.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot add props to an image — "
            "use a provider that implements add_prop()"
        )


class VLMProvider(ABC):
    """Looking at a picture and saying what is in it.

    Used by the identity check (pipeline/identity.py): a shot whose picture
    was generated has to be confirmed to still show this pet, and only this
    pet. Kept as thin as LLMProvider.complete — prompt in, text out — so the
    question being asked lives with the pipeline logic that knows what a
    good answer is, not inside a vendor adapter.
    """

    @abstractmethod
    def inspect_image(self, image_path: str, prompt: str) -> str:
        """Return the model's raw answer about the image at image_path."""

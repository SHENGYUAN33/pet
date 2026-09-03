"""Deciding where a composed element goes, so it does not cover the animal.

Every overlay template used to place itself: the sidebar was always top-right,
the held quote always dead centre. That works until the animal is where the
panel is — and in a shelter's photos it often is. The centred quote landed
across a cat lying horizontally; the right-hand sidebar landed on a cat
sitting on the right. The old safe band only kept elements clear of *other
text* (the pet's details above, the subtitle below); nothing kept them clear
of the pet.

The pet's own silhouette was already available — background replacement and
props both segment it — it simply was not being used for this. So: each
template offers several places it would be willing to sit, and the one with
the least of the animal under it wins.

Kept pure and grid-based on purpose. Scoring against a coarse occupancy grid
rather than against an image means the interesting logic can be tested with a
grid written by hand, with no model, no GPU and no picture — and it means the
same decision is reached every time, which resume and single-shot
regeneration both need.
"""

from __future__ import annotations

import enum
from pathlib import Path

#: Grid resolution. Fine enough to tell "the cat is on the left" from "the cat
#: is centre-left", coarse enough that scoring a dozen candidate boxes is
#: arithmetic rather than image processing. At a 1080x1920 frame each cell is
#: about 45x46 px.
GRID_COLS = 24
GRID_ROWS = 42


class Anchor(str, enum.Enum):
    """A place an element is willing to sit.

    Named rather than free coordinates because a template's candidates are a
    design decision — a contact card belongs at the bottom of the frame
    whichever corner it ends up in, and letting the scorer put it anywhere
    would trade one problem for a worse one.
    """

    TOP_LEFT = "top_left"
    TOP_CENTRE = "top_centre"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    #: "middle_centre" rather than "centre": every value is <vertical>_<horizontal>
    #: so the two properties below can read them the same way. A one-word
    #: value here was the exception that broke them.
    CENTRE = "middle_centre"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTRE = "bottom_centre"
    BOTTOM_RIGHT = "bottom_right"

    @property
    def horizontal(self) -> float:
        """0.0 flush left, 0.5 centred, 1.0 flush right."""
        return {"left": 0.0, "centre": 0.5, "right": 1.0}[self.value.split("_")[1]]

    @property
    def vertical(self) -> float:
        """0.0 at the top of the band, 0.5 centred, 1.0 at the bottom."""
        return {"top": 0.0, "middle": 0.5, "bottom": 1.0}[self.value.split("_")[0]]


class Occupancy:
    """A coarse map of what is already spoken for in the frame.

    Values are 0..1 per cell: how much of that cell is the animal (or an
    element already placed). Immutable — `with_box` returns a new one — so a
    caller can place a panel, mark it taken, and place the stickers against
    the result without the two steps interfering.
    """

    __slots__ = ("cells", "cols", "rows")

    def __init__(self, cells: tuple[tuple[float, ...], ...]):
        self.cells = cells
        self.rows = len(cells)
        self.cols = len(cells[0]) if cells else 0

    @classmethod
    def empty(cls, cols: int = GRID_COLS, rows: int = GRID_ROWS) -> Occupancy:
        """Nothing known to be in the way.

        What a caller gets when the mask could not be produced — a stopped
        ComfyUI, a photo the segmenter found nothing in. Every candidate then
        scores the same and the template's first choice wins, which is
        precisely the old fixed behaviour. Losing the mask costs the
        improvement, never the video.
        """
        return cls(tuple(tuple(0.0 for _ in range(cols)) for _ in range(rows)))

    @classmethod
    def from_mask(cls, mask_path: str | Path, cols: int = GRID_COLS, rows: int = GRID_ROWS):
        """Read a subject mask into a grid, or None if it cannot be read.

        Boundary: the file comes from an external tool. None rather than an
        exception, because "we do not know where the pet is" is a state this
        module is designed to handle (see `empty`), not a failure.
        """
        try:
            from PIL import Image

            with Image.open(mask_path) as image:
                # Averaging downscale, so a cell's value is the share of it the
                # mask covers rather than whatever one sampled pixel happened
                # to be.
                small = image.convert("L").resize((cols, rows), Image.BOX)
                data = list(small.getdata())
        except Exception:  # noqa: BLE001 - boundary: an image file from another process
            return None

        return cls(
            tuple(
                tuple(data[row * cols + col] / 255.0 for col in range(cols)) for row in range(rows)
            )
        )

    def coverage(self, box: tuple[float, float, float, float]) -> float:
        """How much of this box is already taken, 0..1.

        box is (left, top, right, bottom) in fractions of the frame. Cells are
        weighted by how much of them the box actually overlaps, so a panel
        that clips the edge of the animal scores lower than one sitting on top
        of it — which is the difference the ranking has to see.
        """
        left, top, right, bottom = box
        total = 0.0
        weight = 0.0
        for row in range(self.rows):
            cell_top, cell_bottom = row / self.rows, (row + 1) / self.rows
            row_overlap = min(bottom, cell_bottom) - max(top, cell_top)
            if row_overlap <= 0:
                continue
            for col in range(self.cols):
                cell_left, cell_right = col / self.cols, (col + 1) / self.cols
                col_overlap = min(right, cell_right) - max(left, cell_left)
                if col_overlap <= 0:
                    continue
                area = row_overlap * col_overlap
                total += self.cells[row][col] * area
                weight += area
        return total / weight if weight else 0.0

    def with_box(self, box: tuple[float, float, float, float]) -> Occupancy:
        """A copy with this box marked fully taken.

        How one element gets out of another's way: place the panel, mark it,
        then place the marks against the result. Without it two elements that
        both dodge the animal happily land on each other.
        """
        left, top, right, bottom = box
        cells = []
        for row in range(self.rows):
            cell_top, cell_bottom = row / self.rows, (row + 1) / self.rows
            new_row = []
            for col in range(self.cols):
                cell_left, cell_right = col / self.cols, (col + 1) / self.cols
                overlaps = min(bottom, cell_bottom) > max(top, cell_top) and min(
                    right, cell_right
                ) > max(left, cell_left)
                new_row.append(1.0 if overlaps else self.cells[row][col])
            cells.append(tuple(new_row))
        return Occupancy(tuple(cells))


def anchor_box(
    anchor: Anchor,
    size: tuple[float, float],
    band: tuple[float, float] = (0.0, 1.0),
    margin: float = 0.0,
) -> tuple[float, float, float, float]:
    """Where an element of this size sits at this anchor, in fractions.

    band is the vertical range it may occupy — the region left over once the
    pet's details and the subtitle have taken the top and bottom of the frame.
    An element taller than the band is pinned to the top of it rather than
    allowed to overflow: overflowing puts it on the subtitle, which is the one
    thing the band exists to prevent.
    """
    element_width, element_height = size
    band_top, band_bottom = band

    free_width = max(0.0, 1.0 - element_width - margin * 2)
    left = margin + free_width * anchor.horizontal

    free_height = max(0.0, (band_bottom - band_top) - element_height)
    top = band_top + free_height * anchor.vertical

    return left, top, left + element_width, top + element_height


def choose_anchor(
    candidates: list[Anchor],
    size: tuple[float, float],
    occupancy: Occupancy,
    *,
    band: tuple[float, float] = (0.0, 1.0),
    margin: float = 0.0,
    tolerance: float = 0.05,
) -> Anchor:
    """The candidate with least of the animal under it.

    Ties keep the earliest candidate, and `tolerance` makes near-ties count as
    ties: a placement that is 2% better but moves the panel to the opposite
    corner is not better, it is just different, and a video whose sidebar
    jumps corner to corner between shots looks broken. The order of
    `candidates` is therefore the design's own preference, and the scoring
    only overrules it when it clearly should.

    An empty candidate list is a programming error rather than a runtime
    condition, so it raises instead of inventing a position.
    """
    if not candidates:
        raise ValueError("choose_anchor needs at least one candidate")

    scored = [
        (occupancy.coverage(anchor_box(anchor, size, band, margin)), index, anchor)
        for index, anchor in enumerate(candidates)
    ]
    best_score = min(score for score, _, _ in scored)
    # Everything within tolerance of the best is "as good as"; the earliest of
    # those — the design's first choice — wins.
    return min(
        (item for item in scored if item[0] <= best_score + tolerance),
        key=lambda item: item[1],
    )[2]


def pick_slots(
    slots: list[tuple[float, float, float, float]],
    occupancy: Occupancy,
    count: int,
) -> list[tuple[float, float, float, float]]:
    """The `count` emptiest of these boxes, each marked taken as it is chosen.

    For the stickers, which are several small marks rather than one panel.
    Chosen one at a time against an occupancy that grows, so two marks cannot
    both pick the same clear corner.
    """
    remaining = list(slots)
    current = occupancy
    chosen: list[tuple[float, float, float, float]] = []
    for _ in range(min(count, len(remaining))):
        best = min(remaining, key=current.coverage)
        chosen.append(best)
        remaining.remove(best)
        current = current.with_box(best)
    return chosen


def painted_box(image_path: str | Path) -> tuple[float, float, float, float] | None:
    """The region a full-frame transparent layer actually painted, in fractions.

    Used to keep the stickers off the panel: the panel picks its own position
    from several candidates, so nothing else knows where it ended up, and
    asking the finished PNG is both exact and free of a second source of truth
    that could drift from it.

    None if the file cannot be read or nothing was painted.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            box = image.convert("RGBA").getchannel("A").getbbox()
            if box is None:
                return None
            width, height = image.size
    except Exception:  # noqa: BLE001 - boundary: a file another step wrote
        return None

    left, top, right, bottom = box
    return left / width, top / height, right / width, bottom / height

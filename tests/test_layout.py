from __future__ import annotations

import pytest

from pipeline.layout import Anchor, Occupancy, anchor_box, choose_anchor, pick_slots


def _grid(rows: list[str]) -> Occupancy:
    """Build an occupancy from ASCII, so a test can say where the pet is.

    '#' is the animal, '.' is clear — the whole point of scoring against a
    grid rather than an image is that this can be written by hand.
    """
    return Occupancy(tuple(tuple(1.0 if char == "#" else 0.0 for char in row) for row in rows))


LEFT_HALF = _grid(["##..", "##..", "##..", "##.."])
RIGHT_HALF = _grid(["..##", "..##", "..##", "..##"])
TOP_HALF = _grid(["####", "####", "....", "...."])
MIDDLE_BAND = _grid(["....", "####", "####", "...."])
EMPTY = _grid(["....", "....", "....", "...."])


# --- reading the grid -------------------------------------------------------


def test_an_empty_frame_is_clear_everywhere():
    assert Occupancy.empty().coverage((0.0, 0.0, 1.0, 1.0)) == 0.0


def test_coverage_sees_where_the_animal_is():
    assert LEFT_HALF.coverage((0.0, 0.0, 0.5, 1.0)) == pytest.approx(1.0)
    assert LEFT_HALF.coverage((0.5, 0.0, 1.0, 1.0)) == pytest.approx(0.0)


def test_a_box_clipping_the_animal_scores_between():
    """A panel that catches the edge of the animal has to score lower than one
    sitting on top of it — that difference is what the ranking reads."""
    clipping = LEFT_HALF.coverage((0.375, 0.0, 0.875, 1.0))
    assert 0.0 < clipping < 1.0


def test_an_unreadable_mask_is_not_an_error(tmp_path):
    """ "We do not know where the pet is" is a state this module handles, not
    a failure — a stopped ComfyUI must not cost the video."""
    assert Occupancy.from_mask(tmp_path / "nothing.png") is None


def test_a_real_mask_file_is_read(tmp_path):
    from PIL import Image

    mask = tmp_path / "mask.png"
    image = Image.new("L", (100, 100), 0)
    image.paste(255, (0, 0, 50, 100))  # animal on the left
    image.save(mask)

    occupancy = Occupancy.from_mask(mask)

    assert occupancy is not None
    assert occupancy.coverage((0.0, 0.0, 0.4, 1.0)) > 0.9
    assert occupancy.coverage((0.6, 0.0, 1.0, 1.0)) < 0.1


# --- anchoring --------------------------------------------------------------


def test_an_anchor_puts_an_element_where_its_name_says():
    size = (0.4, 0.2)
    left, top, right, bottom = anchor_box(Anchor.TOP_LEFT, size)
    assert (left, top) == pytest.approx((0.0, 0.0))
    assert (right, bottom) == pytest.approx((0.4, 0.2))

    left, top, right, bottom = anchor_box(Anchor.BOTTOM_RIGHT, size)
    assert (right, bottom) == pytest.approx((1.0, 1.0))


def test_an_element_stays_inside_its_band():
    """Above the band are the pet's details, below it the subtitle."""
    box = anchor_box(Anchor.BOTTOM_CENTRE, (0.4, 0.2), band=(0.2, 0.8))
    assert box[3] == pytest.approx(0.8)

    box = anchor_box(Anchor.TOP_CENTRE, (0.4, 0.2), band=(0.2, 0.8))
    assert box[1] == pytest.approx(0.2)


def test_an_element_taller_than_its_band_is_pinned_to_the_top():
    """Overflowing downward puts it on the subtitle, which is the one thing
    the band exists to prevent."""
    box = anchor_box(Anchor.BOTTOM_CENTRE, (0.4, 0.9), band=(0.2, 0.8))
    assert box[1] == pytest.approx(0.2)


def test_a_margin_keeps_an_element_off_the_frame_edge():
    box = anchor_box(Anchor.TOP_RIGHT, (0.4, 0.2), margin=0.05)
    assert box[2] == pytest.approx(0.95)


# --- choosing ---------------------------------------------------------------


def test_the_panel_moves_away_from_the_animal():
    """The complaint this whole module exists for: a fixed right-hand panel
    lands on a cat sitting on the right."""
    candidates = [Anchor.TOP_RIGHT, Anchor.TOP_LEFT]

    assert choose_anchor(candidates, (0.4, 0.3), RIGHT_HALF) is Anchor.TOP_LEFT
    assert choose_anchor(candidates, (0.4, 0.3), LEFT_HALF) is Anchor.TOP_RIGHT


def test_a_vertical_move_works_the_same_way():
    candidates = [Anchor.TOP_CENTRE, Anchor.BOTTOM_CENTRE]
    assert choose_anchor(candidates, (0.4, 0.3), TOP_HALF) is Anchor.BOTTOM_CENTRE


def test_with_nothing_in_the_way_the_designs_first_choice_wins():
    """Which is exactly the old fixed behaviour — so losing the mask costs
    the improvement, never the layout."""
    candidates = [Anchor.TOP_RIGHT, Anchor.TOP_LEFT, Anchor.BOTTOM_LEFT]
    assert choose_anchor(candidates, (0.3, 0.2), EMPTY) is Anchor.TOP_RIGHT
    assert choose_anchor(candidates, (0.3, 0.2), Occupancy.empty()) is Anchor.TOP_RIGHT


def test_a_near_tie_does_not_move_the_panel():
    """A placement 2% better but in the opposite corner is not better, it is
    just different — and a sidebar that jumps corner to corner between shots
    looks broken."""
    barely = _grid(["#...", "....", "....", "...."])
    candidates = [Anchor.TOP_RIGHT, Anchor.TOP_LEFT]

    # TOP_LEFT is very slightly worse here, but not meaningfully.
    assert choose_anchor(candidates, (0.25, 0.25), barely) is Anchor.TOP_RIGHT


def test_a_clear_difference_does_move_it():
    candidates = [Anchor.TOP_RIGHT, Anchor.TOP_LEFT]
    assert choose_anchor(candidates, (0.4, 0.3), RIGHT_HALF) is Anchor.TOP_LEFT


def test_the_same_input_always_chooses_the_same_place():
    """Resume and single-shot regeneration both re-render shots that already
    exist; a layout that drifted between runs would make them different
    videos."""
    candidates = [Anchor.TOP_RIGHT, Anchor.TOP_LEFT, Anchor.BOTTOM_RIGHT]
    first = choose_anchor(candidates, (0.35, 0.25), MIDDLE_BAND)
    for _ in range(5):
        assert choose_anchor(candidates, (0.35, 0.25), MIDDLE_BAND) is first


def test_no_candidates_is_a_programming_error():
    with pytest.raises(ValueError):
        choose_anchor([], (0.3, 0.2), EMPTY)


# --- getting out of each other's way ----------------------------------------


def test_a_placed_element_is_marked_taken():
    marked = EMPTY.with_box((0.0, 0.0, 0.5, 0.5))
    assert marked.coverage((0.0, 0.0, 0.5, 0.5)) == pytest.approx(1.0)
    assert marked.coverage((0.5, 0.5, 1.0, 1.0)) == pytest.approx(0.0)


def test_marking_does_not_change_the_original():
    EMPTY.with_box((0.0, 0.0, 1.0, 1.0))
    assert EMPTY.coverage((0.0, 0.0, 1.0, 1.0)) == pytest.approx(0.0)


def test_two_marks_do_not_land_in_the_same_clear_corner():
    """Both dodge the animal, and without marking as they go they would
    happily land on each other."""
    slots = [
        (0.0, 0.0, 0.25, 0.25),
        (0.75, 0.0, 1.0, 0.25),
        (0.0, 0.75, 0.25, 1.0),
        (0.75, 0.75, 1.0, 1.0),
    ]

    chosen = pick_slots(slots, RIGHT_HALF, 2)

    assert len(chosen) == 2
    assert len(set(chosen)) == 2
    # Both should be on the clear side.
    assert all(box[2] <= 0.5 for box in chosen)


def test_asking_for_more_marks_than_there_are_slots_returns_what_exists():
    slots = [(0.0, 0.0, 0.25, 0.25)]
    assert len(pick_slots(slots, EMPTY, 5)) == 1


# --- what the templates and marks actually do with it -----------------------


def _cat_on_the_right():
    """A pet occupying the right half of the frame, at real grid resolution."""
    from pipeline.layout import GRID_COLS, GRID_ROWS

    return Occupancy(
        tuple(
            tuple(1.0 if col >= GRID_COLS // 2 else 0.0 for col in range(GRID_COLS))
            for _ in range(GRID_ROWS)
        )
    )


def test_the_sidebar_moves_off_the_animal(tmp_path):
    """The complaint this was built for: a fixed right-hand panel on a cat
    sitting on the right."""
    from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH
    from pipeline.layout import painted_box
    from pipeline.overlay_renderer import (
        OverlayTemplate,
        SceneOverlaySpec,
        render_scene_overlay,
    )

    spec = SceneOverlaySpec(
        template=OverlayTemplate.INFO_SIDEBAR, tags=["年齡：1歲", "疫苗：已完成"]
    )
    pet = _cat_on_the_right()

    def coverage(occupancy, name):
        path = render_scene_overlay(
            spec,
            width=FRAME_WIDTH,
            height=FRAME_HEIGHT,
            accent="0xFF8FA3",
            output_path=tmp_path / f"{name}.png",
            occupancy=occupancy,
        )
        return pet.coverage(painted_box(path))

    fixed = coverage(Occupancy.empty(), "fixed")
    aware = coverage(pet, "aware")

    assert aware < fixed / 2, f"panel barely moved: {fixed:.0%} -> {aware:.0%}"


def test_marks_land_on_the_clear_side(tmp_path):
    from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH
    from pipeline.stickers import stickers_for_scene

    placed = stickers_for_scene(
        "cute", "0xFF8FA3", 0, FRAME_WIDTH, FRAME_HEIGHT, _cat_on_the_right()
    )

    assert placed
    for _, x, _ in placed:
        assert x < FRAME_WIDTH / 2, "a mark landed on the animal's side of the frame"


def test_without_an_occupancy_marks_still_rotate_with_the_shot():
    """The fallback when nothing knows where the pet is: the old behaviour,
    so a six-shot video does not have one mark stuck in one place six times."""
    from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH
    from pipeline.stickers import stickers_for_scene

    first = stickers_for_scene("cute", "0xFF8FA3", 0, FRAME_WIDTH, FRAME_HEIGHT)
    second = stickers_for_scene("cute", "0xFF8FA3", 1, FRAME_WIDTH, FRAME_HEIGHT)

    assert [(x, y) for _, x, y in first] != [(x, y) for _, x, y in second]

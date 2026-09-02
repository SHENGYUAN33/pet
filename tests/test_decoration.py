"""The layout a shot is dressed in.

Unlike a generated background this is plain compositing, so what is worth
testing is that it is applied where it belongs, that it never lands on top of
something a viewer has to read, and that it can be switched off.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pipeline import config, decoration
from pipeline.editing import FRAME_HEIGHT, FRAME_WIDTH, build_scene_clip
from pipeline.profile import PetProfile

EXAMPLE_PROFILE = {
    "pet_id": "PET-DECOR",
    "name": "豆豆",
    "species": "dog",
    "breed": "米克斯",
    "sex": "male",
    "age": "2歲",
    "size": "medium",
    "health_status": {"vaccinated": True, "neutered": True, "microchipped": True},
    "personality_tags": {"appeal": [], "lifestyle_fit": [], "care_needs": [], "restrictions": []},
    "adoption_requirements": [],
    "contact_url": "https://example.org/adopt/PET-DECOR",
    "media": {"assets": []},
    "identity_card": {},
}


def test_each_narrative_style_has_its_own_accent():
    accents = {style: decoration.palette_for(style)["accent"] for style in config.DECOR_PALETTES}

    assert len(set(accents.values())) == len(accents), "styles that look identical are one style"


def test_an_unknown_style_looks_ordinary_rather_than_failing():
    """A model can write anything into `style`; that is a reason to look
    plain, not a reason to lose the video."""
    assert decoration.palette_for("no-such-style") == decoration.palette_for(
        config.DECOR_DEFAULT_STYLE
    )


def test_the_identity_line_says_what_an_adopter_needs_first():
    line = decoration.identity_line(PetProfile.model_validate(EXAMPLE_PROFILE))

    assert "豆豆" in line
    assert "2歲" in line
    assert "米克斯" in line
    assert "男生" in line, "sex is stored in English and read by adopters in Chinese"


def test_the_identity_line_leaves_out_what_the_profile_does_not_have():
    """A blank where a breed should be looks like a mistake in the video."""
    profile = dict(EXAMPLE_PROFILE, breed=None)

    line = decoration.identity_line(PetProfile.model_validate(profile))

    assert "米克斯" not in line
    assert not line.endswith("·")
    assert " ·  · " not in line


def test_the_border_stays_inside_the_delivery_frame():
    """Drawn inside the picture rather than added around it: every clip has
    to stay exactly the output size or concatenation stops stream-copying."""
    chain = decoration.border_filter("0xFF8FA3", FRAME_WIDTH, FRAME_HEIGHT)

    inset = config.DECOR_BORDER_INSET
    assert f"x={inset}:y={inset}" in chain
    assert f"w={FRAME_WIDTH - inset * 2}" in chain
    assert f"h={FRAME_HEIGHT - inset * 2}" in chain


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)
class TestRenderedShot:
    @staticmethod
    def _photo(tmp_path):
        photo = tmp_path / "photo.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:s=640x480:d=1",
                "-frames:v",
                "1",
                str(photo),
            ],
            check=True,
            capture_output=True,
        )
        return photo

    def test_a_dressed_shot_is_still_exactly_the_delivery_size(self, tmp_path):
        out = tmp_path / "clip.mp4"

        build_scene_clip(
            visual_path=str(self._photo(tmp_path)),
            duration=1.0,
            subtitle_text="字幕",
            output_path=str(out),
            accent_colour="0xFF8FA3",
            info_card_text="豆豆 · 2歲",
        )

        size = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert size == f"{FRAME_WIDTH}x{FRAME_HEIGHT}"

    def test_the_info_card_is_held_only_while_the_viewer_is_deciding(self, tmp_path):
        """It competes with the hook. After the hook has landed the viewer
        already knows the name, and the card is in the way of the shot."""
        out = tmp_path / "clip.mp4"
        # Made before the recorder goes on: this helper shells out to ffmpeg
        # itself, and its call would otherwise be the one captured.
        photo = self._photo(tmp_path)
        recorded = {}
        real_run = subprocess.run

        def capture(cmd, *args, **kwargs):
            if cmd and cmd[0] == "ffmpeg":
                recorded.setdefault("cmd", cmd)
            return real_run(cmd, *args, **kwargs)

        from pipeline import editing

        editing.subprocess.run = capture
        try:
            build_scene_clip(
                visual_path=str(photo),
                duration=1.0,
                subtitle_text="字幕",
                output_path=str(out),
                accent_colour="0xFF8FA3",
                info_card_text="豆豆 · 2歲",
            )
        finally:
            editing.subprocess.run = real_run

        assert f"lt(t,{config.DECOR_INFO_CARD_SECONDS})" in " ".join(recorded["cmd"])

    def test_an_undressed_shot_has_no_border_or_vignette(self, tmp_path):
        """Switching decoration off has to actually leave the picture alone."""
        out = tmp_path / "clip.mp4"
        # Made before the recorder goes on: this helper shells out to ffmpeg
        # itself, and its call would otherwise be the one captured.
        photo = self._photo(tmp_path)
        recorded = {}
        real_run = subprocess.run

        def capture(cmd, *args, **kwargs):
            if cmd and cmd[0] == "ffmpeg":
                recorded.setdefault("cmd", cmd)
            return real_run(cmd, *args, **kwargs)

        from pipeline import editing

        editing.subprocess.run = capture
        try:
            build_scene_clip(
                visual_path=str(photo),
                duration=1.0,
                subtitle_text="字幕",
                output_path=str(out),
            )
        finally:
            editing.subprocess.run = real_run

        chain = " ".join(recorded["cmd"])
        assert "vignette" not in chain
        assert "drawbox" not in chain


def test_a_chosen_accent_beats_the_styles_own():
    """The reviewer's taste is something nothing else in the pipeline can
    know, so it wins — the same way a named scene beats the script."""
    assert decoration.resolve_accent("cute", "0x123456") == "0x123456"


def test_a_blank_choice_is_not_a_colour():
    """An empty form field means "I didn't pick one", not "paint it with
    nothing"."""
    assert decoration.resolve_accent("cute", "   ") == decoration.palette_for("cute")["accent"]
    assert decoration.resolve_accent("cute", None) == decoration.palette_for("cute")["accent"]


def test_a_chosen_border_width_is_used():
    chain = decoration.border_filter("0xFF8FA3", FRAME_WIDTH, FRAME_HEIGHT, thickness=20)

    assert ":t=20" in chain


def test_no_chosen_width_falls_back_to_the_configured_one():
    chain = decoration.border_filter("0xFF8FA3", FRAME_WIDTH, FRAME_HEIGHT)

    assert f":t={config.DECOR_BORDER_WIDTH}" in chain


def test_marks_stay_clear_of_the_text_bands():
    """The top of the frame is the pet's details and the AI disclosure; the
    bottom is the subtitle. A mark landing on either covers something a
    viewer has to read."""
    from pipeline.stickers import placement_slots

    size = config.DECOR_STICKER_SIZE
    for _, y in placement_slots(FRAME_WIDTH, FRAME_HEIGHT, size):
        assert y >= config.DECOR_STICKER_SAFE_TOP
        assert y + size <= FRAME_HEIGHT - config.DECOR_STICKER_SAFE_BOTTOM


def test_marks_move_between_shots():
    """The same mark stuck in the same corner six times reads as a watermark
    rather than as decoration."""
    from pipeline.stickers import stickers_for_scene

    first = stickers_for_scene("cute", "0xFF8FA3", 0, FRAME_WIDTH, FRAME_HEIGHT)
    second = stickers_for_scene("cute", "0xFF8FA3", 1, FRAME_WIDTH, FRAME_HEIGHT)

    assert [(x, y) for _, x, y in first] != [(x, y) for _, x, y in second]


def test_a_quiet_style_carries_fewer_marks():
    """A video about an animal that has had a hard time should not be
    covered in sparkles."""
    from pipeline.stickers import stickers_for_scene

    cute = stickers_for_scene("cute", "0xFF8FA3", 0, FRAME_WIDTH, FRAME_HEIGHT)
    warm = stickers_for_scene("warm_story", "0xE0A458", 0, FRAME_WIDTH, FRAME_HEIGHT)

    assert len(warm) < len(cute)


def test_marks_are_tinted_to_the_video_they_belong_to():
    """Drawn per accent so they belong to the video rather than sitting on
    top of it — and cached, so the same video always gets the same marks."""
    from pipeline.stickers import sticker_path

    pink = sticker_path("heart", "0xFF8FA3")
    teal = sticker_path("heart", "0x4FB3A9")

    assert pink != teal
    assert pink.exists() and teal.exists()
    assert sticker_path("heart", "0xFF8FA3") == pink


def test_an_unknown_shape_says_what_there_is():
    from pipeline.stickers import sticker_path

    with pytest.raises(ValueError, match="heart"):
        sticker_path("unicorn", "0xFF8FA3")


def test_stickers_can_be_switched_off(monkeypatch):
    from pipeline.stickers import stickers_for_scene

    monkeypatch.setattr(config, "DECOR_STICKERS_ENABLED", False)

    assert stickers_for_scene("cute", "0xFF8FA3", 0, FRAME_WIDTH, FRAME_HEIGHT) == []

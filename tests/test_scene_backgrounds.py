"""Where a shot's background comes from, and what may be invented in it.

The script now carries a background per scene, which is what lets a video
move through places instead of repeating one setting. Two things have to
hold for that to be safe: the reviewer's own instruction must beat the
script's, and an invented setting must not quietly make a claim about the
pet that its Profile never made.
"""

from __future__ import annotations

from pipeline import config
from pipeline.background import BackgroundMode, resolve_scene_background
from pipeline.fact_check import find_background_risks
from pipeline.qa import validate_script_structure


def _scene(scene_id: int, mode: str | None = None, prompt: str | None = None) -> dict:
    scene = {"scene_id": scene_id, "start": 0, "end": 5, "subtitle": "字幕"}
    if mode is not None:
        scene["background"] = {"mode": mode, "prompt": prompt}
    return scene


def test_a_scene_takes_the_treatment_its_script_asked_for():
    background = resolve_scene_background(_scene(1, "extend", "a cat in a cosy living room"))

    assert background is not None
    assert background.mode is BackgroundMode.EXTEND
    assert background.prompt == "a cat in a cosy living room"


def test_the_script_may_not_replace_a_setting_on_its_own():
    """Replacing works only as well as the pet segments out of that
    particular photo, and the script model never sees the photo — it reads
    filenames and a Profile. The shot is still made, with its real
    background kept and its margins filled."""
    background = resolve_scene_background(_scene(1, "replace", "a cosy living room"))

    assert background.mode is BackgroundMode.EXTEND


def test_the_downgrade_is_reported_rather_than_silent():
    issues = validate_script_structure({"scenes": [_scene(1, "replace", "a cosy living room")]})

    assert any("only a reviewer" in issue for issue in issues)


def test_a_reviewer_may_still_replace_a_setting():
    """They have looked at the photograph, which is the whole difference."""
    background = resolve_scene_background(
        _scene(1, "keep"),
        override_scenes={1},
        override_mode=BackgroundMode.REPLACE,
        override_prompt="a cosy living room",
    )

    assert background.mode is BackgroundMode.REPLACE


def test_the_film_wide_look_reaches_every_generated_shot():
    """art_direction is what keeps six shots looking like one video rather
    than six; a prompt that doesn't carry it defeats the point of having it."""
    background = resolve_scene_background(
        _scene(1, "replace", "green grass in a park"),
        art_direction="warm afternoon light, shallow depth of field",
    )

    assert (
        background.prompt == "green grass in a park, warm afternoon light, shallow depth of field"
    )
    assert background.prompt.startswith("green grass"), "the shot's own subject leads"


def test_keep_means_the_photograph_is_shown_as_photographed():
    assert resolve_scene_background(_scene(1, "keep")) is None


def test_a_scene_with_no_background_block_gets_none():
    assert resolve_scene_background(_scene(1)) is None


def test_a_reviewers_instruction_beats_the_script():
    """Naming scenes on the command line is how someone corrects one shot,
    so it has to win over what the script decided for it."""
    background = resolve_scene_background(
        _scene(1, "extend", "a cat indoors"),
        override_scenes={1},
        override_mode=BackgroundMode.REPLACE,
        override_prompt="a sunny park",
    )

    assert background.mode is BackgroundMode.REPLACE
    assert background.prompt == "a sunny park"


def test_an_override_reaches_a_scene_the_script_said_nothing_about():
    background = resolve_scene_background(
        _scene(2),
        override_scenes={2},
        override_mode=BackgroundMode.EXTEND,
    )

    assert background.mode is BackgroundMode.EXTEND
    assert background.prompt is None, "the provider's own default wording is fine"


def test_an_unknown_mode_shows_the_photograph_rather_than_failing():
    """A model can write anything. Losing the whole render over one bad word
    is worse than showing the photo — pipeline/qa.py reports it instead."""
    assert resolve_scene_background(_scene(1, "surprise_me", "...")) is None


def test_an_unknown_mode_is_reported():
    issues = validate_script_structure({"scenes": [_scene(1, "surprise_me", "...")]})

    assert any("surprise_me" in issue for issue in issues)


def test_a_treatment_that_describes_nothing_is_reported():
    """It would silently fall back to the provider's generic wording, which
    is not the park the script asked for."""
    issues = validate_script_structure({"scenes": [_scene(1, "replace", "  ")]})

    assert any("describes nothing" in issue for issue in issues)


def test_one_description_reused_across_shots_is_reported():
    """The point of moving backgrounds into the script was so the video could
    travel; the same sentence on every shot is the old single-prompt
    behaviour wearing the new schema."""
    issues = validate_script_structure(
        {
            "scenes": [
                _scene(1, "replace", "a blurred park"),
                _scene(2, "replace", "A Blurred Park"),
                _scene(3, "replace", "a sunlit kitchen"),
            ]
        }
    )

    repeated = [issue for issue in issues if "share one background description" in issue]
    assert len(repeated) == 1
    assert "3" not in repeated[0], "only the shots that actually repeat"


def test_a_video_where_every_setting_is_invented_is_reported():
    """A shelter's video should show an adopter this animal somewhere it has
    actually been."""
    issues = validate_script_structure(
        {"scenes": [_scene(1, "replace", "a park"), _scene(2, "replace", "a kitchen")]}
    )

    assert any("no shot shows the pet where it really is" in issue for issue in issues)


def test_an_invented_setting_may_not_imply_a_fact_about_the_pet():
    """A living room with a child in it claims this animal is good with
    children. Nobody promised that, and the Profile does not say it."""
    risks = find_background_risks(
        {"scenes": [_scene(1, "replace", "a bright living room with a child playing")]}
    )

    assert len(risks) == 1
    assert "child" in risks[0]


def test_an_invented_setting_may_not_summon_a_second_animal():
    """Naming an animal in the prompt makes the model paint one, so the shot
    ends up with a cat that does not exist beside the real one."""
    risks = find_background_risks({"scenes": [_scene(1, "replace", "a park with another cat")]})

    assert risks and "cat" in risks[0]


def test_a_plain_setting_raises_nothing():
    risks = find_background_risks(
        {"scenes": [_scene(1, "replace", "green grass in a sunny park, blurred trees behind")]}
    )

    assert risks == []


def test_only_invented_settings_are_second_guessed():
    """EXTEND continues the photograph the camera took: whatever is in it was
    already there, and objecting to it is not this check's business."""
    risks = find_background_risks(
        {"scenes": [_scene(1, "extend", "a cat lying on a bed beside its owner")]}
    )

    assert risks == []


def test_forbidden_terms_match_whole_words_only():
    """Substring matching would fire on "humid" for "human" and make the
    check something people learn to ignore."""
    assert "human" in config.BACKGROUND_FORBIDDEN_TERMS

    risks = find_background_risks({"scenes": [_scene(1, "replace", "a humid greenhouse")]})

    assert risks == []


def test_a_background_risk_is_recorded_apart_from_a_missing_disclosure(tmp_path, monkeypatch):
    """They reach the reviewer as different warnings because the fix is
    different: a missing disclosure is reworded in the narration, an invented
    claim is removed from it, and a risky setting is reworded in the
    background. Merging them would tell someone to edit the wrong thing."""
    from pipeline import pet_repo

    written = {}

    class FakeJob:
        disclosure_missing = None
        structure_issues = None
        script_json = None
        work_dir = None

    def fake_session():
        raise AssertionError("not used")

    job = FakeJob()
    monkeypatch.setattr(pet_repo, "_require_job", lambda session, job_id: job)
    monkeypatch.setattr(pet_repo, "get_session", lambda: _NullSession(written))

    pet_repo.record_job_script(
        1,
        script_json={},
        work_dir=str(tmp_path),
        disclosure_missing=["不與其他貓咪同住"],
        background_risks=["scene 2: generated background mentions child"],
        structure_issues=[],
    )

    assert job.disclosure_missing == {
        "missing_restrictions": ["不與其他貓咪同住"],
        "background_risks": ["scene 2: generated background mentions child"],
        "unsupported_claims": [],
    }


class _NullSession:
    """Stands in for a database session so record_job_script's field
    assignments can be checked without PostgreSQL."""

    def __init__(self, written):
        self.written = written

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_every_treatment_has_a_label_for_the_progress_line():
    """The enum values are wire format; a progress line reading "generating
    replace background" mid-sentence in Chinese is not something a reviewer
    should have to parse."""
    for mode in BackgroundMode:
        assert mode.label and mode.label != mode.value

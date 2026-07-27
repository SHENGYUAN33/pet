from pipeline.qa import validate_script_structure


def _scene(scene_id, start, end, subtitle="有內容"):
    return {"scene_id": scene_id, "start": start, "end": end, "subtitle": subtitle}


def _script(scenes, duration=30):
    return {"duration": duration, "scenes": scenes}


def test_valid_six_scene_script_has_no_issues():
    script = _script(
        [
            _scene(1, 0, 5),
            _scene(2, 5, 10),
            _scene(3, 10, 15),
            _scene(4, 15, 20),
            _scene(5, 20, 25),
            _scene(6, 25, 30),
        ]
    )
    assert validate_script_structure(script) == []


def test_flags_too_many_scenes():
    """Regression case: qwen2.5:7b-instruct generated 11 scenes for a 30s
    video, well past the 5-7 shot target in docs/architecture.md §2."""
    scenes = [_scene(i, i * 3, (i + 1) * 3) for i in range(11)]
    script = _script(scenes, duration=33)

    issues = validate_script_structure(script)

    assert any("scene count 11" in issue for issue in issues)


def test_flags_too_few_scenes():
    script = _script([_scene(1, 0, 15), _scene(2, 15, 30)])
    issues = validate_script_structure(script)
    assert any("scene count 2" in issue for issue in issues)


def test_flags_gap_between_scenes():
    """Regression case: qwen2.5 left a 1s gap between scenes (scene ending
    at 3, next starting at 4), which made the concatenated video shorter
    than the declared duration."""
    script = _script(
        [
            _scene(1, 0, 3),
            _scene(2, 4, 8),
            _scene(3, 8, 13),
            _scene(4, 13, 18),
            _scene(5, 18, 30),
        ]
    )
    issues = validate_script_structure(script)
    assert any("scene 2 starts at 4, expected 3" in issue for issue in issues)


def test_flags_overlap_between_scenes():
    script = _script(
        [
            _scene(1, 0, 5),
            _scene(2, 5, 10),
            _scene(3, 10, 15),
            _scene(4, 15, 20),
            _scene(5, 18, 30),
        ]
    )
    issues = validate_script_structure(script)
    assert any("scene 5 starts at 18, expected 20" in issue for issue in issues)


def test_flags_empty_subtitle():
    script = _script(
        [
            _scene(1, 0, 5),
            _scene(2, 5, 10, subtitle=""),
            _scene(3, 10, 15),
            _scene(4, 15, 20),
            _scene(5, 20, 25),
            _scene(6, 25, 30),
        ]
    )
    issues = validate_script_structure(script)
    assert any("scene 2 has an empty subtitle" in issue for issue in issues)


def test_flags_total_duration_mismatch():
    script = _script(
        [
            _scene(1, 0, 5),
            _scene(2, 5, 10),
            _scene(3, 10, 15),
            _scene(4, 15, 20),
            _scene(5, 20, 22),
        ],
        duration=30,
    )
    issues = validate_script_structure(script)
    assert any("does not match declared duration 30" in issue for issue in issues)

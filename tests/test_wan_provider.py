"""WanProvider's availability check.

The provider deliberately does not start ComfyUI itself (see STARTUP.md), so
"the server isn't running" is the failure users hit most often — it has to
say how to fix it rather than surfacing a raw connection error.
"""

from __future__ import annotations

import pytest
import requests

from pipeline import config
from providers.video.wan_provider import WanProvider, _with_camera_constraint


def test_preflight_explains_how_to_start_a_stopped_comfyui(monkeypatch):
    def refuse(url, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", refuse)

    with pytest.raises(RuntimeError) as excinfo:
        WanProvider().preflight()

    message = str(excinfo.value)
    assert "not reachable" in message
    assert "main.py --listen" in message, "must tell the user how to start the server"


def test_preflight_passes_when_the_server_answers(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: None)

    WanProvider().preflight()


def test_camera_constraint_is_appended_to_the_callers_prompt():
    """A motion description shouldn't have to defend against the model
    answering with camera work — the constraint rides along with every
    prompt, after the caller's own words."""
    result = _with_camera_constraint("貓輕輕搖尾巴、抬頭看鏡頭")

    assert result.startswith("貓輕輕搖尾巴、抬頭看鏡頭")
    assert config.WAN_PROMPT_SUFFIX.strip() in result


def test_camera_constraint_is_not_added_twice():
    once = _with_camera_constraint("貓輕輕搖尾巴")

    assert _with_camera_constraint(once) == once


def test_camera_constraint_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "WAN_PROMPT_SUFFIX", "")

    assert _with_camera_constraint("貓輕輕搖尾巴") == "貓輕輕搖尾巴"


def test_negative_prompt_does_not_penalize_stillness():
    """Wan's published negative prompt tells the model stillness is bad,
    which makes it move the camera to comply — the one motion this pipeline
    never wants."""
    for anti_still in ("静态", "静止"):
        assert anti_still not in config.WAN_NEGATIVE_PROMPT
    assert "镜头运动" in config.WAN_NEGATIVE_PROMPT

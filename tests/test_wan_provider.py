"""WanProvider's availability check.

The provider deliberately does not start ComfyUI itself (see STARTUP.md), so
"the server isn't running" is the failure users hit most often — it has to
say how to fix it rather than surfacing a raw connection error.
"""

from __future__ import annotations

import pytest
import requests

from providers.video.wan_provider import WanProvider


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

"""The page's JavaScript has to parse.

There is no build step for webapp/static/index.html — the browser is the
first thing that ever reads it — so a syntax error anywhere in the single
<script> block stops every handler in the file, and the page loads looking
fine while nothing works. That is exactly what happened: a string literal
ended up containing a real newline, and the pet list sat on "載入中…"
forever with the API answering 200 the whole time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parent.parent / "webapp" / "static" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (only needed to parse the page's JS)"
)


def _page_scripts() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    assert blocks, "index.html has no inline script — did the page structure change?"
    return "\n".join(blocks)


def test_page_javascript_parses(tmp_path):
    extracted = tmp_path / "index_extracted.js"
    extracted.write_text(_page_scripts(), encoding="utf-8")

    result = subprocess.run(
        ["node", "--check", str(extracted)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, f"index.html's JavaScript does not parse:\n{result.stderr}"

"""Talking to the locally-hosted ComfyUI server.

Two providers now run through the same server — Wan2.2 image-to-video
(providers/video/wan_provider.py) and SDXL outpainting
(providers/image/comfy_outpaint_provider.py) — and the parts that are about
*the server* rather than about either model are the same for both: upload an
image, queue a prompt graph, wait for it, explain how to start the server
when it isn't running. That lives here so a provider only has to describe
its own graph.

This module is deliberately not an adapter itself: it has no
provider-interface methods and nothing in pipeline/ imports it. It is the
transport the ComfyUI-backed adapters in providers/ are built on.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import requests

from pipeline import config

#: Identifies this project's jobs in the server's queue. ComfyUI uses it to
#: route websocket progress events; nothing here listens for those, so it
#: only needs to be stable and recognisable in the server log.
CLIENT_ID = "pet-adoption-video"


class ComfyUIClient:
    """Thin HTTP client for one ComfyUI server.

    Never starts the server — same as pipeline/rendering.py not starting
    Ollama or PostgreSQL (see STARTUP.md).
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or config.WAN_COMFYUI_URL

    def ping(self) -> None:
        """Raise with the start command if the server isn't answering.

        A fast check, not a guarantee: the server can still go away between
        here and the actual call, which is why callers keep their own error
        handling around the call itself.
        """
        try:
            requests.get(f"{self.base_url}/system_stats", timeout=5)
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"ComfyUI server not reachable at {self.base_url} — start it first: "
                f"cd {config.WAN_COMFYUI_DIR} && .venv/Scripts/activate && "
                f"python main.py --listen 127.0.0.1 --port 8188"
            ) from e

    def node_options(self, class_type: str, input_name: str) -> list[str]:
        """The values a node's dropdown input currently offers — which for a
        model-loader node is the list of model files actually installed.

        Lets a provider say "that checkpoint isn't there, here is where to
        put it" before queueing work, instead of surfacing ComfyUI's own
        validation error minutes later.
        """
        resp = requests.get(f"{self.base_url}/object_info/{class_type}", timeout=10)
        resp.raise_for_status()
        entry = resp.json()[class_type]["input"]["required"][input_name]

        # Two schema shapes are in play. Older nodes declare the choices
        # inline as the first element; nodes written against the newer
        # IO.Schema API declare the literal string "COMBO" there and put the
        # choices in the options dict beside it. Reading only the first form
        # silently reports "nothing installed" for the second — which reads
        # as a missing model file and sends people off to re-download
        # something they already have.
        if isinstance(entry[0], list):
            return list(entry[0])
        if len(entry) > 1 and isinstance(entry[1], dict):
            return list(entry[1].get("options", []))
        return []

    def upload_image(self, image_path: str) -> str:
        """Upload a local image into the server's input directory, return the
        filename a LoadImage node should reference."""
        with open(image_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/upload/image",
                files={"image": (Path(image_path).name, f)},
                timeout=60,
            )
        resp.raise_for_status()
        return resp.json()["name"]

    def fetch_output(self, file_meta: dict, output_path: str) -> str:
        """Copy one file named in a history entry's outputs to output_path.

        Some node types report a "fullpath" in their history entry, which is
        a direct filesystem copy — valid because the server is local. The
        core save nodes report only filename/subfolder/type, so those are
        fetched over /view instead. Handling both here keeps the choice out
        of every provider.
        """
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        fullpath = file_meta.get("fullpath")
        if fullpath and Path(fullpath).exists():
            shutil.copyfile(fullpath, destination)
            return str(destination)

        resp = requests.get(
            f"{self.base_url}/view",
            params={
                "filename": file_meta["filename"],
                "subfolder": file_meta.get("subfolder", ""),
                "type": file_meta.get("type", "output"),
            },
            timeout=120,
        )
        resp.raise_for_status()
        destination.write_bytes(resp.content)
        return str(destination)

    def run(self, prompt_graph: dict, *, poll_interval: float = 2.0) -> dict:
        """Queue an API-format prompt graph and block until it finishes,
        returning its history entry (which carries the output file paths).

        Build the graph by node-parameter *name*, never by copying a UI
        workflow's widgets_values positions — see
        providers/video/wan_provider.py's docstring for what that cost the
        first time.
        """
        resp = requests.post(
            f"{self.base_url}/prompt",
            json={"prompt": prompt_graph, "client_id": CLIENT_ID},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ComfyUI rejected the workflow: {resp.text}")
        prompt_id = resp.json()["prompt_id"]

        while True:
            resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=30)
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("completed") is True:
                    return entry
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI job {prompt_id} failed: {status}")
            time.sleep(poll_interval)

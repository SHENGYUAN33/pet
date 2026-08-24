"""In-process background tasks for the review UI.

Video generation takes tens of seconds (minutes once Image-to-Video is
involved), and the web UI is the only interface a shelter volunteer has — so
the HTTP request must not be the thing that waits. Requests start a task and
return immediately; the browser polls for progress.

What lives here is only the *live* view of a running thread: the progress
percentage and the current step. That is in-process on purpose — a restart
kills the thread, so persisting "60% done" would preserve a number about
work that is no longer happening.

Durable state lives in the database instead: pipeline.pet_repo opens a
GenerationJob row (and one SceneJob row per scene) when a run starts and
closes it when it ends, so a run interrupted by a restart is visible
afterwards and can be continued from the scenes it finished
(webapp.main's startup reaper plus pipeline.resume).

Still not the full state machine of docs/architecture.md §10 — there is no
queue (a task starts the moment it is created) and no distribution across
workers. A multi-user deployment needs Celery/Temporal, but the HTTP
surface here (start → poll → result) is the shape a queued implementation
would expose.

Only one task runs at a time: generation saturates the GPU/CPU (I2V models,
FFmpeg), so running two concurrently would just make both slower and risk
CUDA OOM.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from typing import Any

from pipeline.progress import ProgressCallback

# Finished tasks stay readable so a browser that was closed mid-run can still
# collect the result; the oldest are dropped once this many have accumulated.
MAX_REMEMBERED_TASKS = 50

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}


class TaskBusyError(RuntimeError):
    """Raised when a task is requested while another is still running."""


def _prune_locked() -> None:
    finished = [t for t in _tasks.values() if t["status"] != "running"]
    for task in sorted(finished, key=lambda t: t["created_at"])[
        : max(0, len(finished) - MAX_REMEMBERED_TASKS)
    ]:
        _tasks.pop(task["task_id"], None)


def running_task() -> dict[str, Any] | None:
    with _lock:
        for task in _tasks.values():
            if task["status"] == "running":
                return dict(task)
    return None


def get_task(task_id: str) -> dict[str, Any] | None:
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task else None


def list_tasks(pet_id: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        tasks = [dict(t) for t in _tasks.values() if pet_id is None or t["pet_id"] == pet_id]
    return sorted(tasks, key=lambda t: t["created_at"], reverse=True)


def start_task(
    *,
    kind: str,
    pet_id: str,
    label: str,
    work: Callable[[ProgressCallback], dict[str, Any]],
) -> dict[str, Any]:
    """Run work() on a background thread, reporting progress into the task
    record. work receives the progress callback to hand to the pipeline and
    returns the JSON-serialisable result the browser will collect."""
    with _lock:
        _prune_locked()
        for task in _tasks.values():
            if task["status"] == "running":
                raise TaskBusyError(task["label"])

        task_id = uuid.uuid4().hex[:12]
        record = {
            "task_id": task_id,
            "kind": kind,
            "pet_id": pet_id,
            "label": label,
            "status": "running",
            "message": "排隊中…",
            "progress": 0.0,
            "created_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        _tasks[task_id] = record

    def report(message: str, fraction: float) -> None:
        with _lock:
            task = _tasks.get(task_id)
            if task is not None and task["status"] == "running":
                task["message"] = message
                task["progress"] = max(0.0, min(1.0, fraction))

    def run() -> None:
        try:
            result = work(report)
            status, error = "done", None
        except Exception as e:  # noqa: BLE001 - boundary: anything the pipeline raises
            # The pipeline's failure modes here are external (Ollama down,
            # ComfyUI not running, FFmpeg/CUDA errors) and the reviewer can
            # only act on them if the message survives to the browser.
            traceback.print_exc()
            result, status, error = None, "error", f"{type(e).__name__}: {e}"
        with _lock:
            # The record can be gone (server shutdown clears it) and a thread
            # that dies here would take its traceback with it, so treat a
            # missing record as "nobody is waiting for this result".
            task = _tasks.get(task_id)
            if task is None:
                return
            task["status"] = status
            task["result"] = result
            task["error"] = error
            task["finished_at"] = time.time()
            if status == "done":
                task["progress"] = 1.0
                task["message"] = "完成"
            else:
                task["message"] = "失敗"

    threading.Thread(target=run, name=f"task-{task_id}", daemon=True).start()
    return get_task(task_id)  # type: ignore[return-value]

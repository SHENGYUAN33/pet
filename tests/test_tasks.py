"""Background task registry + progress plumbing.

These are what make the web UI usable for runs that take minutes: the request
returns immediately and the browser polls, so nothing depends on a blocking
HTTP request staying open.
"""

from __future__ import annotations

import threading
import time

import pytest

from pipeline.progress import scaled
from webapp import tasks


@pytest.fixture(autouse=True)
def _clear_tasks():
    with tasks._lock:
        tasks._tasks.clear()
    yield
    with tasks._lock:
        tasks._tasks.clear()


def _wait_for(task_id: str, status: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tasks.get_task(task_id)
        if task is not None and task["status"] == status:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never reached status {status!r}")


def test_start_task_returns_immediately_and_reports_progress():
    release = threading.Event()

    def work(on_progress):
        on_progress("跑到一半", 0.5)
        release.wait(timeout=5)
        return {"job_id": 42}

    started = tasks.start_task(kind="generate", pet_id="PET-1", label="產生新影片", work=work)

    assert started["status"] == "running"
    assert started["result"] is None

    deadline = time.time() + 5
    while time.time() < deadline and tasks.get_task(started["task_id"])["progress"] == 0.0:
        time.sleep(0.02)
    mid = tasks.get_task(started["task_id"])
    assert mid["message"] == "跑到一半"
    assert mid["progress"] == pytest.approx(0.5)

    release.set()
    done = _wait_for(started["task_id"], "done")
    assert done["result"] == {"job_id": 42}
    assert done["progress"] == 1.0
    assert done["error"] is None


def test_failed_task_keeps_the_error_message_for_the_browser():
    def work(on_progress):
        raise RuntimeError("Ollama 沒開")

    started = tasks.start_task(kind="generate", pet_id="PET-1", label="產生新影片", work=work)
    failed = _wait_for(started["task_id"], "error")

    assert "Ollama 沒開" in failed["error"]
    assert failed["result"] is None


def test_second_task_is_rejected_while_one_is_running():
    """Generation saturates the GPU/CPU, so concurrent runs are refused rather
    than queued behind each other unnoticed."""
    release = threading.Event()

    def work(on_progress):
        release.wait(timeout=5)
        return {}

    tasks.start_task(kind="generate", pet_id="PET-1", label="產生新影片", work=work)
    try:
        with pytest.raises(tasks.TaskBusyError):
            tasks.start_task(kind="generate", pet_id="PET-2", label="另一支", work=work)
    finally:
        release.set()


def test_task_can_start_again_after_the_previous_one_finished():
    first = tasks.start_task(kind="generate", pet_id="PET-1", label="第一支", work=lambda p: {})
    _wait_for(first["task_id"], "done")

    second = tasks.start_task(kind="generate", pet_id="PET-1", label="第二支", work=lambda p: {})
    _wait_for(second["task_id"], "done")

    assert {t["label"] for t in tasks.list_tasks()} == {"第一支", "第二支"}
    assert [t["label"] for t in tasks.list_tasks(pet_id="PET-2")] == []


def test_scaled_progress_maps_substep_onto_the_callers_range():
    seen = []
    report = scaled(lambda message, fraction: seen.append((message, fraction)), 0.4, 0.8)

    report("開始", 0.0)
    report("一半", 0.5)
    report("結束", 1.0)

    assert seen == [("開始", 0.4), ("一半", pytest.approx(0.6)), ("結束", 0.8)]

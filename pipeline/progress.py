"""Progress reporting for the long-running generation pipeline.

Generation takes tens of seconds (minutes with Image-to-Video), and the web
UI is the only interface non-technical reviewers have — so the pipeline has
to be able to say where it is, not just block until done. Kept as a plain
optional callback rather than a logging/eventing framework: the CLI passes
nothing and behaves exactly as before, while webapp/tasks.py passes a
callback that updates the task a browser is polling.
"""

from __future__ import annotations

from collections.abc import Callable

# (human-readable step, fraction complete 0.0-1.0)
ProgressCallback = Callable[[str, float], None]


def noop(message: str, fraction: float) -> None:
    """Default callback — used so callers can report unconditionally instead
    of guarding every call site with `if on_progress is not None`."""


def scaled(on_progress: ProgressCallback, low: float, high: float) -> ProgressCallback:
    """Map a sub-step's own 0.0-1.0 progress onto [low, high] of the caller's
    overall progress, so a nested stage (render_script inside generate_video)
    doesn't need to know its share of the whole run."""

    def report(message: str, fraction: float) -> None:
        on_progress(message, low + (high - low) * max(0.0, min(1.0, fraction)))

    return report

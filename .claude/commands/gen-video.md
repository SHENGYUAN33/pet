---
description: Run the PoC pipeline to generate a pet adoption video from a Pet Profile JSON
---

Generate a pet adoption video using the PoC pipeline (`pipeline/run.py`).

Arguments: `$ARGUMENTS` — expected as `<profile.json> <voice_sample.wav> [style] [duration]`
(`style` defaults to `cute`, one of `cute` / `warm_story` / `contrast_humor`; `duration` defaults to `30`).

Steps:
1. Confirm Ollama is reachable and the configured model is pulled: `ollama list` (see `OLLAMA_MODEL` in `.env`).
2. Run: `python -m pipeline.run --profile <profile.json> --voice-sample <voice_sample.wav> --style <style> --duration <duration>`
3. Report the final output path (`storage/output/<pet_id>/<pet_id>_<style>_<duration>s.mp4`), the three generated script variants under `storage/output/<pet_id>/scripts/`, and surface any ffmpeg/TTS/Ollama errors verbatim rather than summarizing them away.

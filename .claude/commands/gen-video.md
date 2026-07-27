---
description: 執行 PoC pipeline，依 Pet Profile JSON 生成寵物領養影片
---

使用 PoC pipeline（`pipeline/run.py`）生成一支寵物領養影片。

參數：`$ARGUMENTS` — 格式為 `<profile.json> <voice_sample.wav> [style] [duration]`
（`style` 預設為 `cute`，可選 `cute` / `warm_story` / `contrast_humor`；`duration` 預設為 `30`）。

步驟：
1. 確認 Ollama 可連線且已 pull 好設定的模型：執行 `ollama list`（模型名稱見 `.env` 的 `OLLAMA_MODEL`）。
2. 執行：`python -m pipeline.run --profile <profile.json> --voice-sample <voice_sample.wav> --style <style> --duration <duration>`
3. 回報最終輸出路徑（`storage/output/<pet_id>/<pet_id>_<style>_<duration>s.mp4`）、`storage/output/<pet_id>/scripts/` 下產生的三種腳本風格，以及原文呈現任何 ffmpeg／TTS／Ollama 的錯誤訊息，不要摘要帶過。

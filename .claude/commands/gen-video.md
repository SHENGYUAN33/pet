---
description: 執行 pipeline，依已匯入資料庫的寵物 ID 生成寵物領養影片
---

使用 pipeline（`pipeline/run.py`）生成一支寵物領養影片。

參數：`$ARGUMENTS` — 格式為 `<pet_id> [voice_sample.wav] [music_track] [style] [duration]`
（`pet_id` 必須已用 `python -m pipeline.manage import-profile` 匯入資料庫；`voice_sample`／`music_track` 省略時分別跳過旁白／背景音樂；`style` 預設為 `cute`，可選 `cute` / `warm_story` / `contrast_humor`；`duration` 預設為 `30`）。

步驟：
1. 確認 PostgreSQL 有起來（`docker compose ps`）且該 `pet_id` 已在資料庫：`python -m pipeline.manage list-pets`，沒有的話先 `python -m pipeline.manage import-profile <path>`。
2. 確認 Ollama 可連線且已 pull 好設定的模型：執行 `ollama list`（模型名稱見 `.env` 的 `OLLAMA_MODEL`）。
3. 執行：`python -m pipeline.run --pet-id <pet_id> [--voice-sample <voice_sample.wav>] [--music-track <music_track>] --style <style> --duration <duration>`
4. 回報最終輸出路徑（`storage/output/<pet_id>/<pet_id>_<style>_<duration>s.mp4`）、`storage/output/<pet_id>/scripts/` 下產生的三種腳本風格、`python -m pipeline.manage show-pet <pet_id>` 可查到的這次生成紀錄，以及原文呈現任何 ffmpeg／TTS／Ollama／DB 的錯誤訊息，不要摘要帶過。

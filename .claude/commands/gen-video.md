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
   - 若使用者要求把某些照片鏡頭做成 AI 動態（策略 B），再加上 `--animate-scenes <逗號分隔的 scene_id> --video-provider <svd|cogvideox|wan> [--animate-prompt "動作描述"]`。`wan` 需要先照 [STARTUP.md](../../STARTUP.md) 啟動 `vendor/comfyui` 伺服器（沒開會直接報錯），一顆鏡頭約 8 分鐘；`svd` 沒有文字條件，`--animate-prompt` 對它無效。預設不加這些旗標，走真實素材剪輯＋照片 Ken Burns。
4. 回報最終輸出路徑（`storage/output/<pet_id>/gen_<token>/<pet_id>_<style>_<duration>s.mp4`）、`storage/output/<pet_id>/scripts/` 下產生的三種腳本風格、印出的 Job id、`python -m pipeline.manage show-pet <pet_id>` 可查到的這次生成紀錄，以及原文呈現任何 ffmpeg／TTS／Ollama／DB 的錯誤訊息，不要摘要帶過。

只想改某個鏡頭而不整支重新生成時，用 Job id 呼叫 `python -m pipeline.regenerate <job_id> <scene_id> [--visual-source <asset_id>] [--subtitle <text>] [--narration <text>] [--voice-sample ...] [--music-track ...] [--animate --video-provider <svd|cogvideox|wan> --animate-prompt <text>]`，不會重跑 LLM，只 patch 指定鏡頭後重新渲染，並留一筆 `parent_job_id` 指回原始 job 的新紀錄（原本的輸出檔案不會被覆蓋）。

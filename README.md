# 貓狗寵物 30 秒領養廣告生成系統

自動化生成貓狗寵物 30 秒（含 15 秒版）領養宣傳短影音，含旁白（寵物第一人稱自我介紹）、字幕、配樂與可愛特效，協助收容所/動保團體提升領養曝光。

- **完整使用手冊（首次安裝、每日啟動、網頁與 CLI 操作、疑難排解）：[docs/manual.md](docs/manual.md)**
- 只要查每天的啟動順序：[STARTUP.md](STARTUP.md)（手冊的精簡版）
- 系統架構與 AI provider 策略：見 [CLAUDE.md](CLAUDE.md)
- 詳細分層設計、旁白 pipeline、開發階段規劃：見 [docs/architecture.md](docs/architecture.md)

目前階段：**MVP 開發中**——以下都已完成並實際跑過：

- 多寵物管理／PostgreSQL 資料層（`pipeline/manage.py` CLI）
- 三種風格腳本（Ollama + Qwen2.5）、第一人稱旁白（Coqui XTTS-v2）、FFmpeg 合成與字幕燒錄
- 分鏡編輯／單鏡頭重生（不重跑 LLM，每次重生留新紀錄不覆蓋舊的）
- Image-to-Video 策略 B：**SVD／CogVideoX／Wan2.2** 三個可切換的開源 provider（Wan2.2 走本機 ComfyUI，文字 prompt 能指揮主體動作）
- 簡易 FastAPI + 網頁介面：中文表單化的 Pet Profile 編輯、素材上傳、背景執行的生成/重生＋進度條、點選式單鏡頭重生

尚未實作：VLM 素材檢查、音樂生成、Provider Router（自動切換）、docs/architecture.md §10 的持久化 Job 狀態機、社群發布與成效追蹤。細節見 [CLAUDE.md](CLAUDE.md) 的「目前所在階段」。

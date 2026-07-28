# 貓狗寵物 30 秒領養廣告生成系統

自動化生成貓狗寵物 30 秒（含 15 秒版）領養宣傳短影音，含旁白（寵物第一人稱自我介紹）、字幕、配樂與可愛特效，協助收容所/動保團體提升領養曝光。

- **要啟動系統/測試，照 [STARTUP.md](STARTUP.md) 的步驟做**
- 系統架構與 AI provider 策略：見 [CLAUDE.md](CLAUDE.md)
- 詳細分層設計、旁白 pipeline、開發階段規劃：見 [docs/architecture.md](docs/architecture.md)

目前階段：**MVP 開發中**——多寵物管理／PostgreSQL 資料層、分鏡編輯／單鏡頭重生、Image-to-Video（SVD/CogVideoX）、簡易 FastAPI + 網頁介面都已完成並驗證過。細節見 [CLAUDE.md](CLAUDE.md) 的「目前所在階段」。

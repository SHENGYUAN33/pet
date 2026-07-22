# 貓狗寵物 30 秒領養廣告生成系統

## 專案簡介
自動化生成貓狗寵物 30 秒（含 15 秒版）領養宣傳短影音的系統。輸入為收容所/送養人上傳的照片、影片、基本資料與健康紀錄；輸出為含旁白（寵物第一人稱自我介紹）、字幕、配樂、可愛特效的成品短影音，並可直接發布至社群平台（TikTok、IG、YT Shorts、LINE、FB）。

目標使用者：收容所、動保團體、送養義工。核心目的：提升寵物曝光與領養轉換率。

> 詳細架構圖與各層規格見 [docs/architecture.md](docs/architecture.md)。

## 系統分層（對應 docs/architecture.md 架構圖）
1. **資料處理層**：上傳、素材品質檢查(VLM)、寵物辨識與特徵抽取、Identity Card 生成、素材分頻與最佳片段選取
2. **內容策劃層（多代理協作）**：Profile / Media Analyst / Persona / Marketing / Script / Storyboard 六個 Agent
3. **生成式 AI 層**：圖像處理/生成、影片生成(I2V/T2V)、TTS 語音生成、音樂/音效
4. **影容製作層**：素材整合、FFmpeg 剪輯、字幕/貼圖/特效、音訊混合、多尺寸版型輸出
5. **品質與安全審查層**：寵物一致性檢查(VLM)、事實正確性檢查、影音品質檢查、內容合規檢查
6. **人工審核層**：影片預覽、腳本/旁白編輯、分鏡調整/重新生成、核准/退回
7. **發布與輸出**：成品影片、封面圖、社群文案、字幕檔、素材與生成紀錄
8. **社群/平台發布**：TikTok、IG、Shorts、LINE、FB、官網/領養平台
9. **成效追蹤與優化**：觀看/留存/互動率、A/B 測試、數據分析報告

基礎設施：Auth/RBAC、Queue/Scheduler、PostgreSQL、Object Storage、Redis、監控。
外部 AI 服務透過 **Provider Adapter** 抽象層接入，商用 API 與開源模型並列可替換（見下）。

## AI Provider 策略：商用 API × 開源模型並列
所有 AI 呼叫都必須經過 `Provider Adapter` 統一介面，禁止在業務程式碼中直接寫死特定廠商 SDK。

路由（Router）依下列條件動態選擇 provider：
- **內容敏感度**：涉及真實寵物臉部/個資 → 優先本地開源，降低資料外流風險
- **時效性**：急件用商用 API；批次任務可排隊跑開源
- **成本上限**：單支影片成本 cap，超過自動降級到開源模型
- **Fallback**：商用 API 失敗/超時 → 自動切開源模型重試

| 功能 | 商用 API | 開源方案 |
|---|---|---|
| LLM（腳本/文案） | Claude / GPT-4o | Llama 3.x、Qwen2.5、Mistral |
| VLM（素材檢查/特徵抽取） | GPT-4V / Claude Vision | Qwen2-VL、LLaVA-NeXT、CogVLM |
| 影片生成 I2V/T2V | Runway、Kling、Luma | Stable Video Diffusion、CogVideoX、Open-Sora、Mochi 1 |
| 圖像生成/增強 | Midjourney API、GPT-Image | SDXL / Flux.1、Real-ESRGAN |
| TTS 旁白 | ElevenLabs、Azure/Google TTS | Coqui XTTS-v2、F5-TTS、GPT-SoVITS、Piper |
| 內容審核 | OpenAI Moderation API | LlamaGuard 3、Detoxify |
| 音樂/音效 | Suno/Udio API | MusicGen |

新增/替換 provider 時：在對應層的 `providers/` 目錄下實作統一介面（輸入輸出 schema 不變），並在 router 設定檔中登記路由規則，不修改呼叫端程式碼。

## 開發階段（Roadmap）
- P0 原型：手動選片 + 開源 TTS + FFmpeg 剪輯模板
- P1 MVP：資料處理層 + 簡化內容策劃(1-2 agent) + 生成AI層(開源優先) + 基本審查
- P2 多代理協作：六個 Agent 全上線 + Provider Router
- P3 品質與安全：自動化審查 + 人工審核 UI
- P4 發布與追蹤：社群平台 API 串接、A/B 測試、儀表板
- P5 持續優化：用成效數據反饋優化模板/人格生成策略

目前所在階段：**P0（尚未開始建置程式碼）**。開發時請先確認目前實際完成到哪個階段，不要假設後期功能已存在。

## 建議技術棧（規劃中，實作時以實際程式碼為準）
- 任務編排：Temporal 或 Celery
- 影片處理：FFmpeg + MoviePy（Python）
- 多代理框架：LangGraph 或 CrewAI
- 開源模型部署：vLLM（LLM/VLM）、ComfyUI（影像/影片 pipeline）
- 後端 API：Python（FastAPI）
- 人工審核 UI：React/Next.js
- 資料庫：PostgreSQL；快取/佇列：Redis；物件儲存：S3 相容(MinIO 或雲端)

## 開發規範
- 業務邏輯一律不得寫死單一 AI 廠商 SDK；一律經 Provider Adapter
- 涉及真實寵物照片/收容所個資的素材，預設走本地開源模型處理，除非該筆資料已標記可送外部 API
- 每個生成的影片都須保留「素材與生成紀錄」（用了哪些 provider、prompt、審核結果），供事後追溯與合規查核
- 人工審核層的退回機制須能標記「部分鏡頭重新生成」，不必整支影片重跑（對應架構圖中 4→5→6 的回饋迴圈）
- 尚未有測試/建置指令可執行；一旦專案初始化出 package.json / pyproject.toml，回頭更新本文件的「常用指令」章節

## 常用指令
（尚未建置，待專案骨架建立後補上 lint / test / build / dev 指令）

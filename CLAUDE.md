# 貓狗寵物 30 秒領養廣告生成系統

## 專案簡介
自動化生成貓狗寵物 30 秒（含 15 秒版）領養宣傳短影音的系統。輸入為收容所/送養人上傳的照片、影片、基本資料與健康紀錄；輸出為含旁白（寵物第一人稱自我介紹）、字幕、配樂、可愛特效的成品短影音，並可直接發布至社群平台（TikTok、IG、YT Shorts、LINE、FB）。

目標使用者：收容所、動保團體、送養義工。核心目的：提升寵物曝光與領養轉換率。

> **這不是「文字轉影片工具」**，而是以寵物真實資料治理為核心的短影音生產平台：
> 資料整理 → 個性分析 → 腳本 → 分鏡 → 影音生成 → 審核 → 發布 → 成效回饋。
> 競爭力來自「真實資料治理＋人格化敘事＋可控分鏡＋人工審核＋領養成效閉環」，不是單一影片模型。

> 完整架構規格見 [docs/architecture.md](docs/architecture.md)（權威文件，細節一律以它為準）。
> Schema 範例：[docs/schemas/pet_profile.example.json](docs/schemas/pet_profile.example.json)、[docs/schemas/script.example.json](docs/schemas/script.example.json)
> Provider Adapter 介面參考：[docs/reference/provider_adapter.py](docs/reference/provider_adapter.py)

## 系統分層（對應 docs/architecture.md §1 架構圖）
1. **資料處理層**：上傳、素材品質檢查(VLM)、寵物辨識與特徵抽取、Identity Card 生成、素材分頻與最佳片段選取
2. **內容策劃層（多代理協作）**：Profile / Media Analyst / Persona / Marketing / Script（3種風格）/ Storyboard（拆5-7鏡頭）/ Fact-check 七個 Agent
3. **生成式 AI 層**：圖像處理/生成、鏡頭級影片生成(I2V/T2V)、TTS 語音生成、音樂/音效
4. **影容製作層**：素材整合、FFmpeg 剪輯、字幕/貼圖/特效、音訊混合、多尺寸版型輸出
5. **品質與安全審查層**：寵物一致性檢查(VLM)、事實正確性檢查、影音品質檢查、內容合規檢查（加權評分，見 §11）
6. **人工審核層**：三欄式介面（Profile／影片／分鏡+QA），影片預覽、腳本/旁白編輯、單鏡頭重新生成、核准/退回
7. **發布與輸出**：成品影片、封面圖、社群文案、字幕檔、素材與生成紀錄
8. **社群/平台發布**：TikTok、IG、Shorts、LINE、FB、官網/領養平台
9. **成效追蹤與優化**：全漏斗指標（曝光→3秒停留→完播→點擊→諮詢→申請→會面→領養）、A/B 測試、公平性監控

基礎設施：Auth/RBAC、Queue/Scheduler、任務狀態機、PostgreSQL、Object Storage、Redis、監控。
外部 AI 服務透過 **Provider Adapter** 抽象層接入，商用 API 與開源模型並列可替換（見下）。

## 關鍵設計決策（不要違背，除非使用者明確要求變更）
- **鏡頭級生成，不做一鏡到底**：每支影片拆成 5-7 個 3-6 秒鏡頭，各自獨立 Job、獨立生成、獨立 QA。失敗只重生失敗鏡頭，不整支重跑（架構理由見 docs/architecture.md §2, §10）
- **腳本一次產生 3 種風格**（萌系/溫暖故事/反差幽默）供人工挑選，不直接發布單一版本
- **Pet Profile 是唯一事實來源**：Fact-check Agent 逐句比對腳本與 Profile，任何內容不得捏造或隱藏必要照護限制條件
- **真實素材優先（策略 A）**：Image-to-Video（策略 B）僅在缺乏真實影片時補位；AI 幻想場景（策略 C）必須標示「部分畫面由 AI 創意生成」
- **QA 加權評分**（Identity Consistency 30% / Factual Correctness 25% / Visual Quality 15% / Audio & Subtitle 10% / Emotional Appeal 10% / Platform Compliance 10%）：低於 80 分不進人工發布畫面；事實正確性或合規檢查任一項失敗，無論總分多少都不可發布
- **人工審核為必經關卡**，不可跳過直接自動發布
- **字幕與文字元件一律由後製引擎疊加**，不讓生成模型在畫面內直接產生文字（避免錯字/字型變形）

## AI Provider 策略：商用 API × 開源模型並列
所有 AI 呼叫都必須經過 `Provider Adapter` 統一介面（見 [docs/reference/provider_adapter.py](docs/reference/provider_adapter.py)），禁止在業務程式碼中直接寫死特定廠商 SDK。

路由（Router）依序評估：**內容敏感度**（涉及真實寵物臉部/個資→優先本地開源）→ **時效性**（急件用商用、批次用開源排隊）→ **成本上限**（超過 cap 自動降級開源）→ **Fallback**（商用失敗/逾時→自動切開源重試）。

| 功能 | 商用 API | 開源方案 |
|---|---|---|
| LLM（腳本/文案/QA判斷） | Claude / GPT-4o | Llama 3.x、Qwen2.5、Mistral（vLLM） |
| VLM（素材檢查/特徵抽取） | GPT-4V / Claude Vision | Qwen2-VL、LLaVA-NeXT、CogVLM |
| 影片生成 I2V/T2V | Runway、Google Veo、Kling、Luma | Stable Video Diffusion、CogVideoX、Open-Sora、Mochi 1 |
| 圖像生成/增強 | Midjourney API、GPT-Image | SDXL / Flux.1、Real-ESRGAN |
| TTS 旁白 | ElevenLabs、Azure/Google TTS | Coqui XTTS-v2、F5-TTS、GPT-SoVITS、Piper |
| 內容審核 | OpenAI Moderation API | LlamaGuard 3、Detoxify |
| 音樂/音效 | Suno/Udio API | MusicGen |
| Embedding | OpenAI/Cohere | BGE、E5 |

新增/替換 provider 時：在對應層的 `providers/` 目錄下實作統一介面（輸入輸出 schema 不變），並在 router 設定檔中登記路由規則，不修改呼叫端程式碼。

## 開發階段（Roadmap，詳見 docs/architecture.md §16）
- **PoC**：單一寵物、真實影片剪輯＋照片 Ken Burns 動態、一版腳本、自動字幕/音樂/CTA、人工核准下載
- **MVP**：多寵物管理、3種腳本風格、Image-to-Video、分鏡編輯、單鏡頭重生、影片 QA、多尺寸輸出
- **正式產品**：多組織權限、社群發布整合、成效儀表板、A/B 測試、多語言（含台語）、對外 API

素材組成建議比例（避免一開始做「完全生成式影片」）：60% 真實影片剪輯 / 20% 照片動態化 / 10% 字幕與貼圖 / 10% AI 生成場景。

目前所在階段：**PoC 開發中**。已建立 `pipeline/`（profile 載入、腳本生成、旁白合成、FFmpeg 剪輯、CLI）與 `providers/`（Ollama LLM、Coqui XTTS-v2 TTS）的最小可行實作，尚未實際跑通端到端流程（需要本機已 `ollama pull` 模型、安裝 `coqui-tts` 及有真實素材）。開發時請先確認目前實際完成到哪個階段，不要假設後期功能已存在。

### PoC 實作邊界（開源優先，對應規劃時的技術選型決策）
- LLM：Ollama + Qwen2.5-7B-Instruct（`pipeline/config.py` 可透過 `.env` 覆寫模型/host）
- TTS：Coqui XTTS-v2（zero-shot voice cloning，需一段參考語音 wav）
- VLM／影片生成 I2V／音樂生成：**PoC 階段刻意省略**，素材品質檢查靠人工選片，影片以真實素材剪輯＋照片 Ken Burns 動態為主（見 docs/architecture.md §5 策略 A）
- 任務編排／資料庫：PoC 用同步流程＋本機資料夾，不上 Celery/Temporal/PostgreSQL，MVP 階段才依規模導入
- Provider Adapter 目前只有單一實作（`providers/llm/ollama_provider.py`、`providers/tts/xtts_provider.py`），Router／多 provider 切換留到 MVP

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
- 每個生成的影片都須保留「素材與生成紀錄」（用了哪些 provider、prompt、seed、模型版本、審核結果），供事後追溯與合規查核
- 人工審核層的退回機制須能標記「單一鏡頭重新生成」，不必整支影片重跑
- 腳本/分鏡一律走結構化 JSON（見 docs/schemas/），不要用純文字文案當作腳本的唯一表示
- 尚未有測試/建置指令可執行；一旦專案初始化出 package.json / pyproject.toml，回頭更新本文件的「常用指令」章節

## 常用指令

```bash
# 環境設定
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
cp .env.example .env   # 依需要調整 OLLAMA_MODEL / XTTS_MODEL_NAME

# 確認 Ollama 模型已就緒（需先安裝並啟動 Ollama）
ollama pull qwen2.5:7b-instruct
ollama list

# 測試
pytest
ruff check .

# 執行 PoC pipeline（需準備好 Pet Profile JSON、真實素材、參考語音 wav）
python -m pipeline.run \
  --profile storage/profiles/<pet_id>.json \
  --voice-sample storage/assets/<pet_id>/voice_ref.wav \
  --style cute \
  --duration 30
# 或在 Claude Code 內用 /gen-video 自訂 slash command
```

素材放置慣例：`storage/profiles/<pet_id>.json`（Profile）、`storage/assets/<pet_id>/`（原始照片/影片/語音樣本，對應 Profile 的 `media.assets[].url` 檔名）、`storage/output/<pet_id>/`（生成結果，含三種風格腳本 JSON、各鏡頭 clip、最終影片）。這三個資料夾內容都被 `.gitignore` 排除，不會進版控。

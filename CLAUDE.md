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

目前所在階段：**MVP 開發中（第三個切片：Image-to-Video + 簡易 FastAPI/前端完成）**。

- 第一個切片（多寵物管理／PostgreSQL 資料層）：`pipeline/db.py`／`pipeline/models.py`／`pipeline/pet_repo.py`，管理 CLI `pipeline/manage.py`（`init-db`／`import-profile`／`list-pets`／`show-pet`）
- 第二個切片（分鏡編輯／單鏡頭重生）：渲染邏輯抽到 `pipeline/rendering.py`（`render_script`，`generate_video` 與 `regenerate_scene` 共用，不重複寫一次），每次生成都在 `storage/output/<pet_id>/gen_<8碼token>/` 有自己的子資料夾（不再共用同一層、不會互相覆蓋鏡頭檔案）。`pipeline/regen.py` 提供 `apply_scene_overrides`（純函式）＋ `regenerate_scene`（patch 單一鏡頭素材/字幕/旁白後只重新渲染，不重跑 LLM），CLI 是 `pipeline/regenerate.py`。`GenerationJob` 現在存完整 `script_json`，`parent_job_id` 把重新生成的版本連回原始 job——**每次重生都是新的一筆紀錄，不覆蓋舊的**，原始輸出檔案與紀錄都保留供追溯。
- 第三個切片（Image-to-Video＋簡易 FastAPI/前端）：
  - **I2V（策略 B）**：`providers/base.py` 新增 `VideoGenerationProvider`，`providers/video/svd_provider.py`（SVD-XT）與 `providers/video/cogvideox_provider.py`（`CogVideoX-5b-I2V`——沒有官方 2B 的 I2V 檢查點）都已實作並可透過 `pipeline/i2v.py` 的 `get_video_provider()` 切換。`pipeline/rendering.py render_script()` 新增 `animate_scenes`/`video_provider` 參數：指定的照片鏡頭會先呼叫 I2V provider 產生暫存影片，再交給既有的 `build_scene_clip`（真實影片那條路徑：loop/裁切/固定fps）處理，沒有另外寫一套邏輯。`pipeline/run.py --animate-scenes 2,4`、`pipeline/regenerate.py --animate` 都可以用。**已在這張機器（RTX 5070 Ti，16GB VRAM）用元寶的照片跑過 SVD 手動 smoke test，確認真的能產生動態效果**；CogVideoX-5b-I2V 目前只實作了介面，還沒手動跑過（5B 模型在 16GB VRAM 上可能吃緊，需要時再驗證）。
  - **簡易 FastAPI/前端**：`webapp/main.py`（FastAPI，直接呼叫既有的 `pipeline.pet_repo`／`pipeline.run`／`pipeline.regen`，不重寫業務邏輯）＋ `webapp/static/index.html`（純 HTML/JS，無建置流程，不是架構文件最終目標的 React/Next.js）。**生成/重生是同步阻塞 API**，還沒有非同步 Job 狀態機，請求會等到 FFmpeg/TTS/LLM 跑完才回應（可能 10-60 秒）。

`GenerationJob` 仍是「完成後留一筆紀錄」的攤平 log，**不是**完整的非同步 Job 狀態機（docs/architecture.md §10 的狀態機仍未實作，目前沒有 PENDING/RUNNING 這種中間狀態，都是跑完才寫進 DB）。開發時請先確認目前實際完成到哪個階段，不要假設後期功能已存在。

⚠️ **目前沒有上 Alembic**：`pipeline/db.py` 的 `init_db()` 只用 `Base.metadata.create_all()`，改 `pipeline/models.py` 的既有表結構（不是新增表）不會自動反映到已存在的資料庫，需要手動 `DROP TABLE` 該表後重跑 `python -m pipeline.manage init-db` 重建（規模還小，先不上遷移工具；正式有資料量後要改用 Alembic）。

### PoC 實作邊界（開源優先，對應規劃時的技術選型決策，MVP 階段陸續補上）
- LLM：Ollama + Qwen2.5-7B-Instruct（`pipeline/config.py` 可透過 `.env` 覆寫模型/host）
- TTS：Coqui XTTS-v2（zero-shot voice cloning，需一段參考語音 wav）
- 影片生成 I2V：**已接**（SVD/CogVideoX，見上），只在明確指定 `animate_scenes`／`--animate` 時才用，預設仍是真實素材剪輯＋照片 Ken Burns（策略 A 優先，I2V 只補位）
- VLM／音樂生成：仍刻意省略，素材品質檢查靠人工選片
- 任務編排：仍是同步流程，Celery/Temporal 留到規模需要時才導入；資料庫用 PostgreSQL（見上）
- Provider Adapter：LLM/TTS/I2V 都已有具體實作（`providers/llm/`、`providers/tts/`、`providers/video/`），但都還是「呼叫端寫死選哪個 provider」，還沒有 Router／依內容敏感度或成本自動切換，留到後續 MVP 切片

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

## Rules & Constraints

### 安全性（硬性規範，不可違背）
- **禁止把秘密寫進程式碼或版控**：API金鑰、資料庫連線字串、TTS/LLM provider token 一律走 `.env`（已被 `.gitignore` 排除）或環境變數，程式碼中只能出現 `os.environ` / `.env` 讀取邏輯，不得出現任何硬編碼金鑰、密碼、connection string
- **真實寵物/收容所個資預設走本地開源模型**（見上方「開發規範」），涉及送外部商用 API 的資料必須先確認該筆已標記可外送；不確定時視為不可外送
- **Provider Adapter 是唯一對外呼叫層**：任何新增的外部 API 呼叫都必須經過 `providers/` 下的 adapter，不得在 `pipeline/` 業務邏輯或 agent 程式碼中直接 import 特定廠商 SDK 或直接發 HTTP request 給外部服務
- **輸入驗證**：所有外部輸入（上傳的照片/影片/JSON、Pet Profile、使用者在審核介面填寫的文字）在進入 pipeline 前都要經過 schema 驗證（見 `docs/schemas/`），不得信任未驗證的輸入直接組字串下 shell 指令或 SQL
- **禁止字串拼接組 SQL**：資料庫查詢一律用參數化查詢（parameterized query）或 ORM，不得用 f-string/字串拼接組 SQL 語句
- **禁止字串拼接組 shell 指令**：呼叫 `ffmpeg`/`ffprobe` 等外部程式時，參數一律用 list 形式傳給 `subprocess`（`shell=False`），不得把使用者可控的檔名/文字直接嵌進 shell 字串
- **輸出內容不得外洩敏感資訊**：日誌、錯誤訊息、生成紀錄不得包含完整 API 金鑰、收容所內部聯絡方式以外的個資（例如飼主電話、地址全文）
- **MCP／外部工具存取需經使用者同意**：`.claude/mcp.json` 內的 postgres／github 等 server 屬範本，實際啟用前需確認連線字串/token 來源安全，且不得指向正式環境資料庫做未經確認的寫入操作

### 程式碼品質（硬性規範）
- **每個新功能／bug fix 都要有對應測試**（`tests/`，用 `pytest`），不接受無測試覆蓋的 pipeline 邏輯變更
- **合併前必須通過 `ruff check` 與 `ruff format --check`**（`.claude/hooks/post-edit.sh` 會在每次 Edit/Write 後自動跑 `ruff check --fix` + `ruff format`，但送出前仍需確認乾淨）
- **型別標註**：`pipeline/` 與 `providers/` 下的公開函式/方法一律加 type hints，資料結構優先用 `pydantic` model（對應 Pet Profile / Script schema），不用裸 `dict`
- **不寫死 magic number／字串**：QA 加權評分門檻（80分）、5-7 鏡頭數量、3-6 秒單鏡頭長度等關鍵參數集中放在 config，不散落在各處程式碼
- **Provider Adapter 介面變更需保持向下相容**：輸入輸出 schema 不可隨意破壞既有呼叫端，新增能力優先用新方法/新欄位而非改變既有介面語意
- **錯誤處理只在系統邊界做**：外部 API 呼叫、檔案 I/O、使用者輸入解析需要 try/except 並記錄可追溯的錯誤上下文；內部函式之間的呼叫信任呼叫端已驗證過的資料，不重複防禦

## 常用指令

```bash
# 環境設定
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
cp .env.example .env   # 依需要調整 OLLAMA_MODEL / XTTS_MODEL_NAME / DATABASE_URL

# 確認 Ollama 模型已就緒（需先安裝並啟動 Ollama）
ollama pull qwen2.5:7b-instruct
ollama list

# 啟動 PostgreSQL（需先手動啟動 Docker Desktop）
docker compose up -d
python -m pipeline.manage init-db

# 匯入寵物 Profile 到資料庫（storage/profiles/*.json 只是匯入格式，不再是 pipeline 讀取來源）
python -m pipeline.manage import-profile storage/profiles/<pet_id>.json
python -m pipeline.manage list-pets
python -m pipeline.manage show-pet <pet_id>   # 看 Profile + 生成歷程

# 測試
pytest
ruff check .

# 執行 pipeline（pet_id 需已匯入資料庫；素材與參考語音 wav 為選用）
python -m pipeline.run \
  --pet-id <pet_id> \
  --voice-sample storage/assets/<pet_id>/voice_ref.wav \
  --music-track storage/assets/<pet_id>/music.mp3 \
  --style cute \
  --duration 30
# 印出的 "Job id" 可用來做單鏡頭重生；或在 Claude Code 內用 /gen-video 自訂 slash command

# 單鏡頭重生（不重跑 LLM，只 patch 指定鏡頭後重新渲染整支影片）
python -m pipeline.regenerate <job_id> <scene_id> \
  --subtitle "新的字幕" \
  --music-track storage/assets/<pet_id>/music.mp3   # voice-sample/music-track 需比照原本生成時再傳一次

# Image-to-Video（需要 CUDA GPU；torch 要裝對應 CUDA 版本的 wheel，不能只 pip install torch）
pip install torch --index-url https://download.pytorch.org/whl/cu128   # 依實際 GPU/CUDA 版本調整
pip install -e ".[i2v]"
python -m pipeline.regenerate <job_id> <scene_id> --animate --video-provider svd   # 或 cogvideox

# 簡易 FastAPI + 前端（無建置流程的純 HTML/JS，不是最終目標的 React/Next.js）
pip install -e ".[web]"
python -m uvicorn webapp.main:app --reload   # 開 http://localhost:8000
# 用 "python -m uvicorn"，不要直接打 "uvicorn"：這台機器 PATH 上可能有其他 Python
# 安裝的 uvicorn.exe，會用錯環境（找不到 psycopg 等套件）
```

素材放置慣例：`storage/assets/<pet_id>/`（原始照片/影片/語音樣本，對應 Profile 的 `media.assets[].url` 檔名）、`storage/output/<pet_id>/gen_<token>/`（每次生成/重生獨立的輸出子資料夾，含三種風格腳本 JSON、各鏡頭 clip、最終影片）。這兩個資料夾內容都被 `.gitignore` 排除，不會進版控。`storage/profiles/*.json` 是匯入資料庫用的格式範本，實際運作時 pipeline 讀的是資料庫，不是這個資料夾。

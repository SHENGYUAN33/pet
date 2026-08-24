# 使用手冊

貓狗寵物 30 秒領養廣告生成系統 — 完整操作說明。

這份文件涵蓋「從一台什麼都沒裝的電腦，到產出第一支影片」的所有步驟。
若只是要快速查啟動順序，看[第 3 章](#3-每天開工的啟動順序)。

- 架構與開發規範：[CLAUDE.md](../CLAUDE.md)
- 分層設計細節：[docs/architecture.md](architecture.md)
- 這份手冊是操作面的權威文件；[STARTUP.md](../STARTUP.md) 是它的精簡版

> **安全提醒**：目前系統**沒有登入機制**。網頁介面預設只綁在 `127.0.0.1`
> （只有本機能連），請**不要**用 `--host 0.0.0.0` 把它開到區域網路或公開網路上——
> 那等於讓任何連得到的人都能上傳檔案、修改寵物資料、啟動生成。

---

## 1. 系統由哪些東西組成

這不是單一程式，而是**四個服務同時運作**（第五個是選用的）。少開任何一個，系統會在用到它的那一步才失敗。

| # | 服務 | 用途 | 一定要開嗎 |
|---|---|---|---|
| 1 | **PostgreSQL**（Docker 容器） | 存寵物資料、生成紀錄、鏡頭紀錄 | 一定要 |
| 2 | **Ollama** | 本機大語言模型，負責寫腳本 | 產生新影片時要 |
| 3 | **Python 虛擬環境 `.venv`** | 所有 Python 套件裝在裡面 | 每個終端機都要啟用 |
| 4 | **uvicorn（FastAPI）** | 網頁介面的後端 | 用網頁操作時要 |
| 5 | **ComfyUI** | Wan2.2 圖生影片伺服器 | 只有用 `wan` 動態化時要 |

另外還需要 **FFmpeg**（不是服務，是命令列工具，裝好在 PATH 上即可）負責所有剪輯、字幕燒錄與混音。

### 各服務沒開的話會怎樣

| 沒開的服務 | 會在哪一步失敗 | 錯誤長相 |
|---|---|---|
| PostgreSQL | 一開始讀寵物資料 | `connection refused` / `could not connect to server` |
| Ollama | 產生腳本（新影片） | 連線錯誤指向 `localhost:11434` |
| `.venv` 未啟用 | 執行任何指令 | `ModuleNotFoundError: No module named 'psycopg'` 之類 |
| FFmpeg | 渲染鏡頭 | `FileNotFoundError: ffmpeg` |
| ComfyUI | 只有動態化鏡頭那一步 | provider 直接報錯提示先啟動 ComfyUI |

> 單鏡頭重生與續跑**不需要 Ollama**（不重跑腳本生成），但仍需要 PostgreSQL 與 FFmpeg。

---

## 2. 首次安裝（每台機器只做一次）

### 2.1 前置軟體

| 軟體 | 版本要求 | 安裝方式 | 驗證指令 |
|---|---|---|---|
| Python | 3.11 以上 | python.org 或 winget | `python --version` |
| Git | 任意 | winget / git-scm.com | `git --version` |
| FFmpeg | 任意近期版本 | `winget install Gyan.FFmpeg` | `ffmpeg -version` |
| Docker Desktop | 任意 | docker.com | `docker --version` |
| Ollama | 任意 | ollama.com | `ollama --version` |

安裝 FFmpeg 後**要開一個新的終端機視窗**才會抓到 PATH。若還是找不到，登出再登入或重開機。

### 2.2 取得程式碼

```powershell
cd C:\Users\tkums\OneDrive\桌面
git clone <repository-url> dogcat
cd dogcat
```

### 2.3 建立虛擬環境並安裝套件

這台機器上有多個 Python 安裝，**一定要用專案自己的 `.venv`**，不要裝進全域環境。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,web]"
```

若 PowerShell 擋下指令碼執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Git Bash 使用者改用：`source .venv/Scripts/activate`

啟用成功的標誌是提示字元前面出現 `(.venv)`。

安裝的三組套件：

- 主套件：pydantic、requests、coqui-tts、sqlalchemy、psycopg、alembic
- `[dev]`：pytest、ruff
- `[web]`：fastapi、uvicorn、python-multipart

### 2.4 設定環境變數

```powershell
copy .env.example .env
```

`.env` 已被版控排除，可以安全地放連線資訊。預設值在本機開發可直接用，
不需要修改也能跑。各欄位說明見[附錄 A](#附錄-a-env-變數對照表)。

**絕對不要把 API 金鑰或連線字串寫進程式碼**，一律放 `.env`。

### 2.5 啟動資料庫並建立資料表

先開 Docker Desktop（GUI 程式，從開始選單或工作列開啟），等系統匣的鯨魚圖示不再有動畫。

```powershell
docker compose up -d
```

容器名稱 `dogcat-postgres-1`，對外 port 是 **5433**（不是預設的 5432 — 這台機器上另一個專案佔用了 5432，特意避開）。

用 `docker ps` 確認狀態是 `Up`，然後建立資料表：

```powershell
python -m pipeline.manage init-db
```

> **`init-db` 只能用在全新的空資料庫。** 它只會補上「缺少的表」，
> 不會修改既有表的結構。已經在用的資料庫要套用結構變更，用的是
> `alembic upgrade head`（見 [5.5](#55-開發與維護指令)）。

### 2.6 下載語言模型

```powershell
ollama pull qwen2.5:7b-instruct
ollama list
```

`ollama list` 要能看到 `qwen2.5:7b-instruct`（約 4.7GB）。

### 2.7 驗收安裝

```powershell
pytest
```

應該看到全部通過（目前 95 項）。不需要 GPU、不需要 Ollama、不需要 FFmpeg，
但**需要 PostgreSQL 已經在跑**（也就是 2.5 要先做完）。

> **已知問題**：資料庫沒開時，測試**不會失敗，但會慢到像當掉**。
> 需要資料庫的那 5 個測試檔（`test_pet_repo`／`test_job_lifecycle`／`test_scene_jobs`／
> `test_migrations`／`test_webapp`）各有一個「連不上就跳過」的判斷，
> 但那個判斷沒有設連線逾時，每一個都要卡好幾分鐘才放棄。
> 實測資料庫關閉時的結果是 **42 passed、53 skipped，花了 21 分 46 秒**（正常應是 8 秒內）。
> 而且收集階段就卡住，過程中畫面完全空白。
> 如果 `pytest` 執行後一直沒有輸出，先確認 `docker ps` 看得到 `dogcat-postgres-1`。

### 2.8 選用：安裝 Image-to-Video（需要 NVIDIA GPU）

只有要把照片做成 AI 動態影片才需要。**torch 必須裝對應 CUDA 版本的 wheel，不能只 `pip install torch`。**

```powershell
# 先裝 torch（RTX 50 系列用 cu128，其他卡依實際 CUDA 版本調整）
pip install torch --index-url https://download.pytorch.org/whl/cu128
# 再裝其餘 I2V 套件（只涵蓋 svd / cogvideox）
pip install -e ".[i2v]"
```

模型會在第一次使用時自動從 Hugging Face 下載（數 GB，要等一段時間）。

### 2.9 選用：安裝 ComfyUI 與 Wan2.2（動作品質最好的選項）

Wan2.2 走**獨立的 ComfyUI 伺服器**，有自己的虛擬環境，跟專案的 `.venv` 完全分開。
`vendor/` 整個目錄不進版控，換機器後要自己重裝。

1. 把 ComfyUI clone 到 `vendor/comfyui/`，依照它自己的說明建立 `.venv` 並安裝依賴。
2. 下載三個檢查點，放到 ComfyUI 對應的模型目錄：

| 檔案 | 放置位置 | 對應設定 |
|---|---|---|
| `Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors` | `models/diffusion_models/WanVideo/2_2/TI2V/` | `WAN_MODEL_FILE` |
| `Wan2_2_VAE_bf16.safetensors` | `models/vae/wanvideo/` | `WAN_VAE_FILE` |
| `umt5-xxl-enc-fp8_e4m3fn.safetensors` | `models/text_encoders/` | `WAN_T5_FILE` |

3. 測試啟動（詳見 [3.6](#36-選用啟動-comfyui)）。

---

## 3. 每天開工的啟動順序

照順序做。每一步都有「怎麼確認成功」。

### 3.1 啟動 Docker Desktop

從開始選單或工作列開啟（GUI 程式，沒有指令可以直接啟動）。

**確認成功**：系統匣的鯨魚圖示靜止不動（不再有動畫）。

### 3.2 啟動 PostgreSQL 容器

```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat
docker compose up -d
```

**確認成功**：

```powershell
docker ps
```

看到 `dogcat-postgres-1`，狀態 `Up`，port 顯示 `0.0.0.0:5433->5432/tcp`。

### 3.3 確認 Ollama 在跑

```powershell
ollama list
```

**確認成功**：列出模型清單，其中有 `qwen2.5:7b-instruct`。
指令失敗代表服務沒起來，到系統匣找 Ollama 圖示啟動。

### 3.4 啟用 Python 虛擬環境

**每一個新開的終端機視窗都要做這步**，不會自動記住。

```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat
.venv\Scripts\Activate.ps1
```

**確認成功**：提示字元前面出現 `(.venv)`。

### 3.5 啟動網頁後端

```powershell
uvicorn webapp.main:app --reload
```

**確認成功**：看到 `Uvicorn running on http://127.0.0.1:8000`。

**這個視窗要保持開著。** `--reload` 會在你改程式碼時自動重載，但終端機一關服務就停。

啟動時如果上一次有生成跑到一半被中斷，會看到類似訊息：

```
[startup] marked 2 interrupted job(s) as failed: [12, 13]
```

這是正常的自動收尾 — 那些工作會在網頁上以「可續跑」的形式出現。

### 3.6 選用：啟動 ComfyUI

只有要用 `wan` 動態化時才需要。**另開一個終端機視窗**：

```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat\vendor\comfyui
.venv\Scripts\Activate.ps1
python main.py --listen 127.0.0.1 --port 8188
```

**確認成功**：看到 `To see the GUI go to: http://127.0.0.1:8188`。
**這個視窗也要保持開著。**

### 3.7 打開瀏覽器

```
http://localhost:8000
```

---

## 4. 用網頁操作

網頁介面是給非工程使用者的主要入口，所有功能都不需要寫 JSON 或打指令。

### 4.1 建立一隻新寵物

左側是寵物清單（上方有搜尋框）。建立新寵物有三種方式：

| 方式 | 適用情境 | 怎麼做 |
|---|---|---|
| 表單新增 | 一般情況 | 直接在右側表單填寫，按儲存 |
| 從檔案匯入 | 已有現成的 Profile JSON | 從下拉選單選 `storage/profiles/` 底下的檔案 |
| JSON 直編 | 進階、批次調整 | 展開「進階」摺疊區貼上 JSON，按「套用到上方表單」後再儲存 |

> 匯入路徑被限制在 `storage/profiles/` 內，且會經過欄位驗證 — 不能拿來讀取其他位置的檔案。

### 4.2 上傳素材

**要先存過這隻寵物、拿到 pet_id，才能上傳。**

在照片／影片清單按「從電腦選擇」，會開啟作業系統的檔案對話框。
選好的檔案會**上傳並存到 `storage/assets/<pet_id>/`**，之後系統使用的是存下來的那一份。

（瀏覽器基於安全理由拿不到本機檔案的真實路徑，所以「選檔案」實際上必然是「上傳」。）

可接受的格式：

| 類型 | 副檔名 |
|---|---|
| 照片 | `.jpg` `.jpeg` `.png` `.webp` `.bmp` |
| 影片 | `.mp4` `.mov` `.m4v` `.mkv` `.avi` `.webm` |
| 音訊 | `.wav` `.mp3` `.m4a` `.aac` `.ogg` `.flac` |

單檔上限 500MB。同名檔案不會覆蓋，會自動加編號。

### 4.3 填寫 Pet Profile

表單分成幾個區塊，欄位意義見[附錄 B](#附錄-b-pet-profile-欄位說明)。三個要特別注意的：

- **必要照護限制（restrictions）**：這是系統唯一會強制檢查是否出現在腳本裡的欄位。
  請寫成**短的關鍵字**（「不親貓」「不與其他貓咪同住」），
  **不要寫成完整句子**（「需要長期服用腎臟處方飼料，不可中斷」）—
  目前的檢查是字串比對，模型換句話說就會誤報。
- **聯絡連結（contact_url）**：會變成影片結尾的行動呼籲，務必填對應這隻寵物的正確網址。
- **外觀特徵（Identity Card）**：目前完全靠人工填寫，系統不會自動辨識。

### 4.4 產生影片

右側切到「產生新影片」分頁：

| 欄位 | 說明 | 建議 |
|---|---|---|
| 風格 | 萌系 / 溫暖故事 / 反差幽默 | 三種都會產生，這裡選的是要拿去渲染的那一種 |
| 時長 | 秒數 | 預設 30，可填 15 |
| 語音樣本 | 一段參考語音 wav，用來克隆音色 | 留空則整支影片無旁白（靜音），適合先測畫面 |
| 配樂 | 背景音樂檔 | 留空則只有旁白 |
| 動態化鏡頭 | 要用 AI 動態化的鏡頭編號 | 留空（預設）＝真實素材剪輯＋照片緩慢推移 |
| 影片模型 | svd / cogvideox / wan | 只有勾了動態化才有作用 |
| 動作描述 | 例：「貓輕輕搖尾巴、抬頭看鏡頭」 | 只對 `wan`、`cogvideox` 有效，`svd` 會忽略 |

按下產生後會**立刻**出現進度條，實際工作在背景執行。進度階段依序是：
腳本 1/3 → 2/3 → 3/3 → 事實與結構檢查 → TTS 旁白 → 鏡頭 1/n … → 合併 → 完成。

**可以關掉進度視窗繼續做別的事**，重新整理頁面也會自動接回還在跑的工作。

**一次只能跑一件**（會吃滿 GPU/CPU）。跑到一半再按第二個會被擋下來並提示目前在跑什麼。

進度百分比只存在伺服器記憶體，**重啟 uvicorn 會掉**。但工作紀錄在資料庫裡，重啟後會被標記為失敗並可續跑。

**大概要等多久**（單機 RTX 5070 Ti）：

| 模式 | 一支 30 秒影片 |
|---|---|
| 預設（無動態化） | 數分鐘，主要花在三次腳本生成與 TTS |
| 含 1 顆 Wan2.2 動態鏡頭 | 約 +8 分鐘 |
| 含 2 顆 Wan2.2 動態鏡頭 | 約 20 分鐘 |

### 4.5 看結果

生成歷程會列出這隻寵物的所有版本，每個版本一張卡片，包含：

- 影片播放器
- 狀態（生成中／完成／失敗）
- **QA 警告**：腳本結構問題（鏡頭數不對、時間軸有空隙、字幕空白）與可能漏掉的必要揭露
- 鏡頭清單，可看到每顆鏡頭用了哪個素材、哪個 provider、什麼 prompt

> QA 警告目前**只是提示，不會擋住流程**。加權評分與 80 分門檻尚未實作，發布前仍須人工判斷。

### 4.6 單鏡頭重生

不滿意某一顆鏡頭時，不需要整支重跑：

1. 先點一個版本
2. 從鏡頭清單點要修改的那一顆
3. 可以換素材（從該寵物已上傳的素材下拉選）、改字幕、改旁白，或勾選 AI 動態化
4. 送出

**不會重跑腳本生成**（不呼叫 Ollama），只 patch 指定鏡頭後重新渲染整支影片。

每次重生都是**一筆新紀錄**，不會覆蓋舊的 — 原本的影片檔案與紀錄都保留，可以回頭比較。

重生時**語音樣本與配樂要重新指定一次**，沒填就等於這支沒有旁白／配樂。

### 4.7 續跑失敗的版本

失敗的版本卡片上會有「↻ 從失敗的鏡頭續跑」。

已經完成的鏡頭會直接沿用（檔案還在），只補跑沒做完的。
一顆 Wan2.2 鏡頭要 8 分鐘，這是這個功能存在的理由。

續跑**不吃任何參數** — 腳本、語音樣本、配樂、動態化設定全部從原本的工作紀錄讀取，
確保續跑出來的是「原本那支影片」而不是一支不一樣的。

---

## 5. 用命令列操作（完整指令參考）

所有指令都要先啟用 `.venv`（[3.4](#34-啟用-python-虛擬環境)）。

### 5.1 管理寵物資料 `pipeline.manage`

```powershell
# 建立資料表（只用於全新的空資料庫）
python -m pipeline.manage init-db

# 匯入一份 Pet Profile JSON（同 pet_id 會覆蓋更新）
python -m pipeline.manage import-profile storage/profiles/PET-2026-001.json

# 列出所有寵物
python -m pipeline.manage list-pets

# 看某隻寵物的完整 Profile 與所有生成紀錄
python -m pipeline.manage show-pet PET-2026-001
```

### 5.2 產生影片 `pipeline.run`

```powershell
python -m pipeline.run --pet-id PET-2026-001 --style cute --duration 30
```

| 旗標 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `--pet-id` | 是 | — | 資料庫裡的寵物 ID |
| `--style` | | `cute` | `cute` / `warm_story` / `contrast_humor` |
| `--duration` | | `30` | 影片秒數 |
| `--voice-sample` | | 無 | 參考語音 wav；省略＝無旁白（靜音） |
| `--music-track` | | 無 | 背景音樂檔；省略＝無配樂 |
| `--animate-scenes` | | 無 | 要動態化的鏡頭編號，逗號分隔，例 `2,4` |
| `--video-provider` | | `svd` | `svd` / `cogvideox` / `wan` |
| `--animate-prompt` | | 無 | 動作描述，只對 `cogvideox` / `wan` 有效 |

完整範例（PowerShell 用反引號換行）：

```powershell
python -m pipeline.run `
  --pet-id PET-2026-001 `
  --voice-sample storage/assets/PET-2026-001/voice_ref.wav `
  --music-track storage/assets/PET-2026-001/music.mp3 `
  --style cute `
  --duration 30 `
  --animate-scenes 2,4 `
  --video-provider wan `
  --animate-prompt "貓輕輕搖尾巴、抬頭看鏡頭"
```

執行完會印出 **Job id**，後續單鏡頭重生要用到。

### 5.3 單鏡頭重生 `pipeline.regenerate`

```powershell
python -m pipeline.regenerate <job_id> <scene_id> --subtitle "新的字幕"
```

| 旗標 | 說明 |
|---|---|
| `job_id`（位置參數） | 原本的生成工作 ID |
| `scene_id`（位置參數） | 要重生的鏡頭編號 |
| `--visual-source` | 換成另一個素材（asset_id 或檔名） |
| `--subtitle` | 新的字幕文字 |
| `--narration` | 新的旁白文字 |
| `--voice-sample` | 要比照原本生成時**再傳一次** |
| `--music-track` | 要比照原本生成時**再傳一次** |
| `--animate` | 把這顆鏡頭的照片做成 AI 動態 |
| `--video-provider` | `svd` / `cogvideox` / `wan`，預設 `svd` |
| `--animate-prompt` | 動作描述 |

```powershell
python -m pipeline.regenerate 12 3 --animate --video-provider wan --animate-prompt "狗狗歪頭看鏡頭"
```

會印出 **New job id** 與它的 parent job id。

### 5.4 續跑失敗的生成 `pipeline.resume`

```powershell
python -m pipeline.resume <job_id>
```

沒有任何選項。所有設定都從工作紀錄讀取。

兩種情況會被拒絕：工作已經完成（沒東西可續）、工作在腳本產生前就失敗（沒有可續的基礎，要重新產生）。

### 5.5 開發與維護指令

```powershell
# 跑測試
pytest
pytest tests/test_webapp.py -q      # 只跑某一個檔案

# 程式碼檢查（合併前必須全綠）
ruff check .
ruff format --check .

# 資料庫結構變更（改了 pipeline/models.py 之後）
alembic revision --autogenerate -m "改了什麼"
alembic upgrade head

# 查目前資料庫的版本
alembic current
```

> 連線字串刻意不寫在 `alembic.ini`（那個檔進版控），而是從 `pipeline/config.py` 注入。
> 測試會擋下「改了 model 卻忘記寫 migration」以及「`alembic.ini` 裡意外出現連線字串」。

---

## 6. 檔案放在哪

```
dogcat/
├─ storage/
│  ├─ profiles/<pet_id>.json          匯入用的 Profile 範本（不是執行時的讀取來源）
│  ├─ assets/<pet_id>/                原始照片、影片、語音樣本、配樂
│  └─ output/<pet_id>/
│     ├─ scripts/                     三種風格的腳本 JSON（最新一次，跨版本共用）
│     └─ gen_<8碼>/                   每次生成／重生獨立的資料夾
│        ├─ audio/scene_N.wav         各鏡頭旁白
│        ├─ scene_N.mp4               各鏡頭剪好的片段
│        ├─ video_only.mp4            串接後的純畫面
│        ├─ narration_full.wav        串接後的旁白
│        ├─ audio_mixed.wav           混音後的音軌
│        └─ <pet_id>_<style>_<秒數>s.mp4   ← 成品
├─ .env                               設定（不進版控）
└─ vendor/comfyui/                    ComfyUI（不進版控，需自行安裝）
```

**執行時 pipeline 讀的是資料庫，不是 `storage/profiles/`。** 那個資料夾只是匯入格式的範本。

`storage/` 底下的內容與 `vendor/` 都被版控排除，不會進 git。

**要備份的是**：PostgreSQL 資料庫（Docker named volume）＋ `storage/assets/` ＋ `storage/output/`。
目前沒有自動備份機制。

---

## 7. 收工時怎麼關

| 項目 | 怎麼關 | 備註 |
|---|---|---|
| 網頁後端 | 在那個終端機視窗按 `Ctrl+C` | — |
| ComfyUI | 同上 | — |
| PostgreSQL | `docker compose stop` | 資料還在，下次 `up -d` 秒開 |
| PostgreSQL（完全移除容器） | `docker compose down` | 資料仍在 named volume 裡，不會消失 |
| Docker Desktop | 不用特別關 | 要關就從系統匣右鍵離開 |
| Ollama | 不用關 | 留著開機自動跑即可 |

**不要在生成進行中關掉 uvicorn** — 那支影片會中斷。真的關掉了，下次啟動時系統會自動把它標記成失敗，可以從失敗的鏡頭續跑。

---

## 8. 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `source 不是可辨識的指令` | 在 PowerShell 打了 Git Bash 語法 | PowerShell 用 `.venv\Scripts\Activate.ps1` |
| `無法載入檔案…因為這個系統上已停用指令碼執行` | PowerShell 執行原則 | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `ModuleNotFoundError: psycopg` 等找不到套件 | 忘記啟用 `.venv`，或被其他 Python 搶先 | 確認提示字元有 `(.venv)` |
| `connection refused` / 連不上資料庫 | Docker 沒開或容器沒起來 | 開 Docker Desktop → `docker compose up -d` → `docker ps` 確認 |
| `failed to connect to the docker API` | Docker Desktop 沒啟動 | 開 Docker Desktop，等鯨魚圖示靜止 |
| `port 5432 already in use` | 跑錯專案的 compose | 本專案用 **5433**，確認在 dogcat 目錄下執行 |
| 腳本生成卡住或連線錯誤指向 11434 | Ollama 沒跑 | `ollama list` 測試，從系統匣啟動 |
| `FileNotFoundError: ffmpeg` | FFmpeg 不在 PATH | 開新終端機；仍失敗則重新登入 Windows |
| `pytest` 長時間沒有輸出（可達 20 分鐘） | PostgreSQL 沒開，跳過判斷沒有連線逾時 | `docker compose up -d` 後重跑；會變回 8 秒內跑完 |
| 影片沒有聲音 | 沒給語音樣本 | 傳 `--voice-sample`；重生時也要再傳一次 |
| 字幕是方框或亂碼 | 字型路徑不對 | 檢查 `.env` 的 `DRAWTEXT_FONT_FILE` 指向存在的中文字型 |
| 網頁一直顯示「生成中」但沒進度 | 上一個 process 死掉留下的紀錄 | 重啟 uvicorn，啟動時會自動收尾成失敗並可續跑 |
| 按產生被擋下來（409） | 已經有一件工作在跑 | 等它跑完，一次只能跑一件 |
| 動態化報錯要求先啟動 ComfyUI | ComfyUI 沒開 | 照 [3.6](#36-選用啟動-comfyui) 啟動 |
| 選了 `cogvideox` 出問題 | 這個 provider 尚未實機驗證過 | 改用 `wan`（品質最好）或 `svd` |
| 影片長度比預期短 | 素材或腳本時間軸問題 | 看版本卡片的 QA 警告是否提示時間軸有空隙 |

---

## 附錄 A：`.env` 變數對照表

| 變數 | 預設值 | 說明 |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 服務位址 |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | 寫腳本用的模型 |
| `XTTS_MODEL_NAME` | `tts_models/multilingual/multi-dataset/xtts_v2` | TTS 模型 |
| `TTS_LANGUAGE` | `zh-cn` | XTTS 的中文語言代碼 |
| `DATABASE_URL` | `postgresql+psycopg://petvideo:changeme@localhost:5433/petvideo` | 資料庫連線 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `petvideo` / `changeme` / `petvideo` | Docker 容器用 |
| `DRAWTEXT_FONT_FILE` | `C:\Windows\Fonts\msjh.ttc` | 字幕字型（微軟正黑體） |
| `MIN_SCENES` / `MAX_SCENES` | `5` / `7` | 每支影片的鏡頭數範圍 |
| `MIN_SCENE_DURATION` / `MAX_SCENE_DURATION` | `3` / `6` | 單顆鏡頭秒數範圍 |
| `SVD_MODEL_NAME` | `stabilityai/stable-video-diffusion-img2vid-xt` | SVD 檢查點 |
| `COGVIDEOX_MODEL_NAME` | `THUDM/CogVideoX-5b-I2V` | CogVideoX 檢查點 |
| `WAN_COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 伺服器位址 |
| `WAN_MODEL_FILE` / `WAN_VAE_FILE` / `WAN_T5_FILE` | 見 `.env.example` | Wan2.2 三個檢查點路徑 |
| `WAN_SAMPLE_STEPS` | `20` | 取樣步數，越高越久越精細 |
| `WAN_WIDTH` / `WAN_HEIGHT` | `704` / `1280` | 動態化的最大邊界框 |
| `WAN_FPS` | `24` | 生成影片的幀率 |

`.env` 已被版控排除。**任何金鑰、密碼、連線字串都只能放這裡，不可寫進程式碼。**

---

## 附錄 B：Pet Profile 欄位說明

| 欄位 | 必填 | 型別 | 說明 |
|---|---|---|---|
| `pet_id` | 是 | 字串 | 唯一識別碼，例 `PET-2026-001` |
| `name` | 是 | 字串 | 寵物名字 |
| `species` | 是 | 字串 | `dog` / `cat` |
| `breed` | | 字串 | 品種，例「米克斯」 |
| `sex` | 是 | 字串 | `male` / `female` |
| `age` | 是 | 字串 | 例「2歲」 |
| `size` | 是 | 字串 | `small` / `medium` / `large` |
| `location` | | 字串 | 所在地 |
| `health_status.vaccinated` | 是 | 布林 | 已施打疫苗 |
| `health_status.neutered` | 是 | 布林 | 已絕育 |
| `health_status.microchipped` | 是 | 布林 | 已植入晶片 |
| `personality_tags.appeal` | | 字串陣列 | 賣點，例「親人」「活潑」 |
| `personality_tags.lifestyle_fit` | | 字串陣列 | 適合的生活型態 |
| `personality_tags.care_needs` | | 字串陣列 | 照護需求 |
| `personality_tags.restrictions` | | 字串陣列 | **必要限制，會被檢查是否出現在腳本裡** |
| `story` | | 字串 | 救援或背景故事 |
| `adoption_requirements` | | 字串陣列 | 領養條件 |
| `contact_url` | 是 | 字串 | 領養聯絡連結，會成為影片結尾的 CTA |
| `media.assets[].asset_id` | 是 | 字串 | 素材識別碼 |
| `media.assets[].type` | 是 | 字串 | `photo` / `video` |
| `media.assets[].url` | 是 | 字串 | 檔案位置；**檔名要對應 `storage/assets/<pet_id>/` 底下實際存在的檔案** |
| `media.assets[].usage_license_status` | | 字串 | 預設 `granted` |
| `media.assets[].ai_extension_allowed` | | 布林 | 是否允許 AI 延伸，預設 `true` |
| `identity_card.*` | | 字串 | 外觀特徵：毛色、花紋、耳型、眼睛顏色、體型、配件、辨識特徵 |

完整範例見 [docs/schemas/pet_profile.example.json](schemas/pet_profile.example.json)。

---

## 附錄 C：Port 一覽

| Port | 服務 | 備註 |
|---|---|---|
| 8000 | 網頁介面（uvicorn） | `http://localhost:8000` |
| 5433 | PostgreSQL | 刻意避開 5432 |
| 11434 | Ollama | 安裝後預設 |
| 8188 | ComfyUI | 只有 Wan2.2 需要 |

---

## 附錄 D：目前的功能邊界

用之前要知道系統**還沒有**的東西：

- **沒有登入機制**，不要開到區域網路
- **沒有自動品質評分**，QA 只會提示不會擋
- **核准／退回沒有留紀錄**，人工審核靠流程約束
- **只輸出 9:16 直式**，沒有 1:1 或 16:9
- **一次只能跑一支影片**，沒有排隊
- **沒有社群平台發布**，成品要自己下載上傳
- **沒有成效追蹤**
- 素材品質、寵物外觀一致性**沒有自動檢查**，靠人工選片

完整盤點與開發規劃見專案的系統現況報告。

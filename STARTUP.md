# 啟動說明

這份文件是「每次要開始工作/測試系統」時照著做的速查表，只講「怎麼把系統跑起來」。

> **完整版在 [docs/manual.md](docs/manual.md)** — 首次安裝、網頁與 CLI 的完整操作說明、欄位對照表與疑難排解都在那裡。架構與開發規範見 [CLAUDE.md](CLAUDE.md)。

## 系統需要哪些東西同時跑著

| 服務 | 用途 | 啟動方式 |
|---|---|---|
| Docker Desktop + PostgreSQL 容器 | 寵物資料/生成紀錄的資料庫 | `docker compose up -d` |
| Ollama | 本機 LLM（腳本生成） | 通常安裝後會常駐在背景，開機自動啟動 |
| Python `.venv` | 專案依賴（FastAPI/SQLAlchemy/diffusers 等都裝在裡面） | 每個新終端機視窗都要手動啟用 |
| `uvicorn` (FastAPI) | 網頁介面的後端伺服器 | `.venv` 啟用後執行 `uvicorn webapp.main:app --reload` |
| ComfyUI 伺服器 | Wan2.2 圖生影片（`--video-provider wan` 才需要，SVD/CogVideoX 不用） | 見下方「啟動 ComfyUI（Wan2.2 用）」 |

---

## 啟動 ComfyUI（只有要用 `--video-provider wan` 時才需要）

Wan2.2 走的是獨立的 ComfyUI 伺服器（`vendor/comfyui/`，有自己的 `.venv`，不是專案主要的 `.venv`）。⚠️ **`vendor/` 整個目錄都在 `.gitignore` 內、不進版控**，換一台機器 clone 這個 repo 後不會有它，要自己另外裝好 ComfyUI 並下載 `pipeline/config.py` 裡 `WAN_MODEL_FILE`／`WAN_VAE_FILE`／`WAN_T5_FILE` 指定的檢查點。裝好後另開一個終端機視窗常駐執行：

```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat\vendor\comfyui
.venv\Scripts\Activate.ps1
python main.py --listen 127.0.0.1 --port 8188
```

看到 `To see the GUI go to: http://127.0.0.1:8188` 就代表啟動成功，**這個視窗要保持開著**。`providers/video/wan_provider.py` 會直接呼叫這個伺服器的 API，沒開的話 `--video-provider wan` 會直接報錯提示你先啟動。

實測數字（RTX 5070 Ti 16GB VRAM）：一顆鏡頭（約 5 秒、20 步取樣）大概 **8 分鐘**（含模型載入），VRAM 用量約 11GB。這是 SVD/CogVideoX 之外唯一「動作品質好、速度也能接受」的選項——細節跟為什麼選這條路（而不是 diffusers 或 Wan 官方 `generate.py`）見 `pipeline/config.py` 的 `WAN_*` 註解區塊。

---

## 每次開始工作的標準流程

### 1. 啟動 Docker Desktop
桌面/工作列找 Docker Desktop 圖示點開（GUI 應用程式，沒有指令可以直接啟動）。等系統匣圖示顯示鯨魚圖案沒有動畫，代表 daemon 已就緒。

### 2. 啟動 PostgreSQL 容器
```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat
docker compose up -d
```
容器叫 `dogcat-postgres-1`，對外 port 是 **5433**（不是預設的 5432——這台機器上還有另一個專案佔用 5432，特意避開）。用 `docker ps` 確認有看到 `dogcat-postgres-1` 且狀態是 `Up`。

### 3. 確認 Ollama 有在跑
```powershell
ollama list
```
能列出 `qwen2.5:7b-instruct` 等模型就代表沒問題。如果指令失敗（連不上），到工作列系統匣找 Ollama 圖示手動啟動，或重新安裝 Ollama 服務。

### 4. 啟用 Python 虛擬環境
**每個新開的終端機視窗都要做這一步**，不會自動記住。

PowerShell：
```powershell
cd C:\Users\tkums\OneDrive\桌面\dogcat
.venv\Scripts\Activate.ps1
```
如果跳出「不允許執行指令碼」的錯誤：
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Git Bash：
```bash
cd /c/Users/tkums/OneDrive/桌面/dogcat
source .venv/Scripts/activate
```

啟用成功後，提示字元前面會出現 `(.venv)`。

### 5. 啟動網頁後端
```powershell
uvicorn webapp.main:app --reload
```
看到 `Uvicorn running on http://127.0.0.1:8000` 就代表成功。**這個視窗要保持開著**（`--reload` 會讓你之後改程式碼自動套用，但終端機不能關）。

### 6. 打開瀏覽器
```
http://localhost:8000
```
左側會列出已匯入的寵物（目前是元寶，上方有搜尋框），點下去可以：
- 播放已生成的影片、看生成歷程（每個版本一張卡片，含 QA 警告）
- **用中文表單編輯 Pet Profile**（基本資料／健康狀態／個性標籤／故事與領養條件／照片影片清單），JSON 直編收在「進階」摺疊區；也可以從 `storage/profiles/` 下拉選一份檔案匯入
- **上傳照片/影片/語音樣本**：按「從電腦選擇」開檔案對話框，檔案會存到 `storage/assets/<pet_id>/`（要先存過這隻寵物、有 pet_id 才能上傳）
- 填表單產生新影片（風格/時長/語音樣本/音樂，選填欄位收在摺疊區）
- 針對單一鏡頭重新生成：先點一個版本，再從鏡頭清單點要改的鏡頭（不用自己記 job id / scene id），可換素材/字幕/旁白，或勾選 AI 動態化（Image-to-Video，可選 provider 與動作描述）

生成與重生都是**背景執行**：按下去會立刻出現進度條（腳本 1/3 → TTS → 鏡頭 n/m → 合併），可以關掉進度視窗繼續做別的事，重新整理頁面也會自動接回還在跑的工作。**一次只能跑一件**（會吃滿 GPU/CPU），跑到一半再按第二個會被擋下來。⚠️ 進度狀態只存在伺服器的記憶體裡，**重啟 uvicorn 會掉**（已完成的工作仍有資料庫紀錄）。

---

## 不透過網頁，改用 CLI 測試（要另開一個終端機視窗，一樣要先做步驟 4）

```powershell
# 列出資料庫裡的寵物
python -m pipeline.manage list-pets

# 看某隻寵物的 Profile 與生成歷程
python -m pipeline.manage show-pet PET-2026-001

# 匯入新的寵物 Profile（第一次要用某隻寵物前）
python -m pipeline.manage import-profile storage/profiles/<pet_id>.json

# 產生一支新影片
python -m pipeline.run --pet-id PET-2026-001 --style cute --duration 30

# 單鏡頭重生（不重跑 LLM，只換指定鏡頭）
python -m pipeline.regenerate <job_id> <scene_id> --subtitle "新的字幕"

# 把某幾個照片鏡頭做成 AI 動態（Image-to-Video）；wan 需要 ComfyUI 先開著
python -m pipeline.run --pet-id PET-2026-001 --animate-scenes 2,4 --video-provider wan --animate-prompt "貓輕輕搖尾巴、抬頭看鏡頭"
python -m pipeline.regenerate <job_id> <scene_id> --animate --video-provider wan --animate-prompt "狗狗歪頭看鏡頭"

# 跑測試
pytest
```

---

## 收工時怎麼關

- **網頁後端**：在那個終端機視窗按 `Ctrl+C`
- **PostgreSQL 容器**：`docker compose stop`（暫停，資料還在，下次 `docker compose up -d` 秒開）；如果要整個移除容器才用 `docker compose down`（資料不會不見，存在 named volume 裡，只是容器本身被刪掉，下次 `up -d` 會重建容器並掛回同一份資料）
- **Docker Desktop**：不用特別關，開著也不太吃資源；真的要關就從系統匣右鍵離開
- **Ollama**：通常留著開機自動跑就好，不用每次關

---

## 常見問題

- **`source` 不是可辨識的指令**：你在 PowerShell 打了 Git Bash 的語法。PowerShell 用 `.venv\Scripts\Activate.ps1`，不是 `source`。
- **`uvicorn`/`pytest` 說找不到套件（例如 psycopg）**：忘記先啟用 `.venv`，或啟用了但這台機器 PATH 上有其他 Python 安裝搶先——確認提示字元前面有沒有 `(.venv)`。
- **`ffmpeg` 找不到**：ffmpeg 是用 `winget install Gyan.FFmpeg` 裝的，正常情況下**開新的終端機視窗**就會自動抓到 PATH，不需要額外設定；如果還是抓不到，代表 PATH 沒有正確更新，重新登入 Windows 或重開機通常能解決。
- **Docker port 衝突（5432 already in use）**：本專案的 PostgreSQL 刻意設定成對外 port **5433**（見 `docker-compose.yml`），不會跟其他專案的 5432 衝突；如果看到衝突訊息，確認你跑的是這個專案的 `docker compose up -d`，不是別的專案。

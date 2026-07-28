# 啟動說明

這份文件是「每次要開始工作/測試系統」時照著做的操作手冊。架構與開發規範見 [CLAUDE.md](CLAUDE.md)，這裡只講「怎麼把系統跑起來」。

## 系統需要哪些東西同時跑著

| 服務 | 用途 | 啟動方式 |
|---|---|---|
| Docker Desktop + PostgreSQL 容器 | 寵物資料/生成紀錄的資料庫 | `docker compose up -d` |
| Ollama | 本機 LLM（腳本生成） | 通常安裝後會常駐在背景，開機自動啟動 |
| Python `.venv` | 專案依賴（FastAPI/SQLAlchemy/diffusers 等都裝在裡面） | 每個新終端機視窗都要手動啟用 |
| `uvicorn` (FastAPI) | 網頁介面的後端伺服器 | `.venv` 啟用後執行 `uvicorn webapp.main:app --reload` |

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
左側會列出已匯入的寵物（目前是元寶），點下去可以：
- 播放已生成的影片、看生成歷程
- 填表單產生新影片（風格/時長/語音檔路徑/音樂路徑）
- 針對單一鏡頭重新生成（換素材/字幕/旁白，或改用 AI 動態化 Image-to-Video）

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

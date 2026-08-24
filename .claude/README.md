# .claude/ 設定檔說明（中文對照）

`settings.json` 與 `mcp.json` 是標準 JSON，**不支援 `//` 或 `#` 註解**——硬加會導致解析失敗、整份設定失效。因此這兩個檔案的欄位說明改放在這份文件，內容變更時請一併更新本文件。

## settings.json —— CLI 權限與自動化 hook

### `permissions.allow`
不需詢問即可自動執行的指令白名單。目前涵蓋：
- `git status/diff/log/branch/show/remote/add/commit/push`：唯讀查詢類全開，`push` 僅允許不帶危險旗標的形式
- `python`／`pip`／`pytest`：pipeline 開發與測試常用指令（含 `python -m pipeline.run*`）
- `ruff`／`black --check`／`mypy`：靜態檢查與格式化（ruff 是本專案實際採用的 linter，見 `pyproject.toml`）
- `ollama list/pull/run`：本地 LLM 模型管理（對應 `providers/llm/ollama_provider.py`）
- `ffmpeg`／`ffprobe`：影片剪輯（影音製作層核心工具）
- `node`／`npm`／`pnpm`（僅版本查詢與 `run`/`install`/`ci`）：預留給日後改寫成 React/Next.js 的審核 UI（目前的 `webapp/static/index.html` 是純 HTML/JS，沒有 `package.json`，這幾條暫時用不到）
- `ls`／`dir`／`pwd`：基本檔案系統查詢
- `docker --version`／`docker compose config`／`docker ps*`：唯讀的 Docker 狀態查詢

### `permissions.ask`
會跳出確認視窗、需要人工同意才能執行的指令：`git push --force`、`git reset/checkout/clean`、`rm`、`docker compose up/down`、`pip install <pkg>`、`npm install <pkg>`（安裝套件與可能改動/刪除工作目錄狀態的操作，一律先問過）。

### `permissions.deny`
永遠禁止，不會詢問也不會執行：強制 push、`rm -rf /*`（防止誤刪整個檔案系統）。

### `hooks.PostToolUse`
在每次 `Edit`／`Write` 之後自動觸發 `.claude/hooks/post-edit.sh`，對剛編輯的檔案跑對應的 linter／formatter（目前是 `.py` 檔用 `ruff check --fix` + `ruff format`）。`statusMessage` 是執行時顯示在畫面上的提示文字。

## mcp.json —— MCP 外部工具（Skills）範本

定義兩個 MCP server，讓 Claude 能力擴充到專案外部系統：

- **`postgres`**：透過 `@modelcontextprotocol/server-postgres` 連線資料庫查 schema／下查詢。連線字串走 `${POSTGRES_CONNECTION_STRING}` 環境變數，**不得**改成直接寫死帳密。對應本專案已導入的 PostgreSQL（`docker-compose.yml`，對外 port 5433；schema 見 `pipeline/models.py`）。
- **`github`**：透過 `@modelcontextprotocol/server-github` 操作 GitHub（讀 Issue/PR 等）。Token 走 `${GITHUB_PERSONAL_ACCESS_TOKEN}` 環境變數。

⚠️ 這兩個 server 目前都是**尚未啟用的範本**（本專案的 PostgreSQL 存取一律走 `pipeline/db.py` 的 SQLAlchemy，不靠這個 MCP server），實際填入連線字串與 token 前，請先確認：
1. 不會指向正式環境（production）資料庫
2. 不會外洩收容所個資或真實寵物照片資料（見 [CLAUDE.md](../CLAUDE.md) 安全性規範）
3. 啟用該 server 前，Claude Code 仍會照正常流程請你確認信任（`enabledMcpjsonServers`），不會自動連線

## commands/ —— 自訂 Slash Commands

- **`/gen-video`**：執行 pipeline 生成寵物領養影片，對應 `pipeline/run.py`（寵物需已用 `python -m pipeline.manage import-profile` 匯入資料庫）。
- **`/refactor`**：依 [CLAUDE.md](../CLAUDE.md) 的 Rules & Constraints 審視目前變更（型別、Provider Adapter 邊界、錯誤處理、安全性、測試覆蓋），並直接套用修正。

## hooks/post-edit.sh —— 編輯後自動化

編輯或寫入檔案後自動觸發，依副檔名執行對應工具：`.py` 檔跑 `ruff check --fix` + `ruff format`；`.ts/.tsx/.js/.jsx` 檔（目前專案尚未有 `package.json`，此段為日後 React/Next.js 審核 UI 預留）跑 eslint/prettier（若存在的話）。詳細中文註解已寫在腳本內。

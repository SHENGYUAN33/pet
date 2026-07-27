---
description: Review the current diff (or a specified file) for type safety, error handling, and test coverage, then refactor
---

請審視目前變更（`git diff`／`git status` 未提交的異動）或 `$ARGUMENTS` 指定的檔案，依 [CLAUDE.md](../../CLAUDE.md) 的 Rules & Constraints 檢查並修正：

1. **Type Safety**：`pipeline/` 與 `providers/` 下的公開函式/方法是否補齊 type hints；資料結構是否該用 `pydantic` model 而非裸 `dict`（對應 `docs/schemas/` 的 Pet Profile / Script schema）。
2. **Provider Adapter 邊界**：是否有業務邏輯直接 import 特定廠商 SDK 或直接發外部 HTTP request，未經過 `providers/` adapter。
3. **Error Handling**：外部 API 呼叫、檔案 I/O、使用者輸入解析是否有 try/except 並記錄可追溯的錯誤上下文；內部函式之間是否有不必要的重複防禦性檢查。
4. **Security**：是否有硬編碼的 secret/API key、字串拼接組 SQL、字串拼接組 shell 指令（`subprocess` 應用 list 形式、`shell=False`）。
5. **Testing**：修改的邏輯是否有對應的 `pytest` 測試（`tests/`）；若缺漏，補上。

完成檢查後，直接套用修正，並用 `ruff check` / `ruff format` 確認乾淨（`post-edit.sh` hook 會自動跑，但送出前仍需確認）。若某項檢查因超出範圍或需求不明而略過，需明確說明原因，不要靜默跳過。

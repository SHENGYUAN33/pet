#!/usr/bin/env bash
# PostToolUse hook：在 Edit/Write/NotebookEdit 之後自動觸發。
# 從 stdin 讀取 hook 的 JSON payload，取出被編輯的檔案路徑，
# 依副檔名執行對應的 linter／formatter。
# 不會因為 lint 錯誤讓工具呼叫失敗——只透過 stderr 把問題回報給 Claude
#（exit 2 = 「阻擋並把 stderr 內容顯示給 Claude 看」）。

set -u

payload="$(cat)"

# 從 tool_input 取出 file_path，不依賴 jq（Windows/Git Bash 環境不保證有安裝）。
file_path="$(printf '%s' "$payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"(.*)"/\1/')"

if [ -z "$file_path" ] || [ ! -f "$file_path" ]; then
  exit 0
fi

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 0

status=0
output=""

case "$file_path" in
  *.py)
    # Python 檔案：跑 ruff 檢查（自動修復可修的問題）+ ruff 格式化
    if command -v ruff >/dev/null 2>&1; then
      lint_out="$(ruff check --fix "$file_path" 2>&1)"
      fmt_out="$(ruff format "$file_path" 2>&1)"
      output="$lint_out"$'\n'"$fmt_out"
      printf '%s\n' "$lint_out" | grep -qE '^Found [1-9]' && status=2
    fi
    ;;
  *.ts|*.tsx|*.js|*.jsx)
    # 前端檔案：目前專案還沒有 package.json，這段先保留供未來 MVP 階段（Next.js 審核介面）使用
    if [ -f package.json ] && command -v npx >/dev/null 2>&1; then
      if npx --no-install eslint --version >/dev/null 2>&1; then
        output="$(npx --no-install eslint --fix "$file_path" 2>&1)"
        [ $? -ne 0 ] && status=2
      fi
      if npx --no-install prettier --version >/dev/null 2>&1; then
        prettier_out="$(npx --no-install prettier --write "$file_path" 2>&1)"
        output="$output"$'\n'"$prettier_out"
      fi
    fi
    ;;
  *)
    exit 0
    ;;
esac

if [ -n "$output" ]; then
  printf '%s\n' "$output" >&2
fi

exit "$status"

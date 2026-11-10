#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN=$(command -v python3.11)
elif command -v uv >/dev/null 2>&1 && uv python find 3.11 >/dev/null 2>&1; then
  PYTHON_BIN=$(uv python find 3.11)
else
  PYTHON_BIN=$(command -v python3 || true)
fi
if [ -z "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  printf '%s\n' '需要 Python 3.11 或更新版本。' >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' '需要安装 Node.js 和 npm。' >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if [ ! -d frontend/node_modules ]; then
  npm ci --prefix frontend
fi

.venv/bin/python -m uvicorn src.server:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
sleep 2
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  printf '%s\n' '后端未能启动，请检查上面的错误信息。' >&2
  exit 1
fi

printf '%s\n' '访问 http://localhost:5173；按 Ctrl+C 停止服务。'
cd frontend
npm run dev -- --host 127.0.0.1 --strictPort

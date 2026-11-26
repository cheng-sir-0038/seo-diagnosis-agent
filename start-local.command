#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.docker/bin"

if [ ! -f .env.local ]; then
  printf '%s\n' '缺少 .env.local。请按 README 的本地启动说明配置后再打开。' >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf '%s\n' 'Docker 未就绪。请打开 Docker Desktop，等它启动后再双击此文件。' >&2
  exit 1
fi

docker compose --env-file .env.local up -d --wait db

WORKSPACE_URL='http://localhost:5173/'
open_when_ready() {
  attempts=0
  while [ "$attempts" -lt 90 ]; do
    if curl -fsS --max-time 2 "$WORKSPACE_URL" >/dev/null 2>&1 &&
       curl -sS --max-time 2 http://127.0.0.1:8000/ >/dev/null 2>&1; then
      open "$WORKSPACE_URL"
      return
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  printf '服务启动后请在浏览器打开 %s\n' "$WORKSPACE_URL"
}

if command -v open >/dev/null 2>&1; then
  open_when_ready &
  browser_waiter=$!
  trap 'kill "$browser_waiter" 2>/dev/null || true' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi

sh start.sh

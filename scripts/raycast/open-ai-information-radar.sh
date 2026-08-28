#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title 打开 AI 信息雷达
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 📡
# @raycast.packageName AI 信息雷达
# @raycast.description 必要时启动本地服务，然后打开 AI 信息雷达

set -u

COMMAND_DIR="$(cd "$(dirname "$0")" && pwd)"
COMMAND_PATH="$COMMAND_DIR/$(basename "$0")"
APP_ROOT="$(cd "$COMMAND_DIR/../.." && pwd)"
GIT_COMMON_DIR="$(/usr/bin/git -C "$APP_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"

if [ -n "$GIT_COMMON_DIR" ]; then
  PROJECT_ROOT="$(cd "$GIT_COMMON_DIR/.." && pwd)"
else
  PROJECT_ROOT="$APP_ROOT"
fi

APP_URL="http://127.0.0.1:8000"
HEALTH_URL="$APP_URL/health"
VENV_UVICORN="$PROJECT_ROOT/.venv/bin/uvicorn"
ENV_FILE="$PROJECT_ROOT/.env"
DATABASE_FILE="$PROJECT_ROOT/data/radar.db"
LOG_FILE="$PROJECT_ROOT/data/radar-server.log"
SERVICE_LABEL="com.xiemengying.ai-information-radar"

if [ "${1:-}" = "--serve" ]; then
  cd "$APP_ROOT" || exit 1
  exec /usr/bin/env \
    DATABASE_URL="sqlite:///$DATABASE_FILE" \
    PYTHONPATH=. \
    "$VENV_UVICORN" app.main:app \
      --host 127.0.0.1 \
      --port 8000 \
      --env-file "$ENV_FILE"
fi

if /usr/bin/curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  /usr/bin/open "$APP_URL"
  echo "AI 信息雷达已打开"
  exit 0
fi

if [ ! -x "$VENV_UVICORN" ]; then
  echo "启动失败：请先在项目中创建 .venv 并安装依赖"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "启动失败：项目根目录缺少 .env"
  exit 1
fi

/bin/mkdir -p "$PROJECT_ROOT/data"
/bin/launchctl remove "$SERVICE_LABEL" >/dev/null 2>&1 || true
/bin/launchctl submit \
  -l "$SERVICE_LABEL" \
  -o "$LOG_FILE" \
  -e "$LOG_FILE" \
  -- "$COMMAND_PATH" --serve

attempt=0
while [ "$attempt" -lt 30 ]; do
  if /usr/bin/curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
    /usr/bin/open "$APP_URL"
    echo "AI 信息雷达已启动并打开"
    exit 0
  fi
  /bin/sleep 0.25
  attempt=$((attempt + 1))
done

echo "启动失败，请查看日志：$LOG_FILE"
exit 1

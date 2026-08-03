#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${DAILY_RSYNC_PROJECT_ROOT:-$SCRIPT_DIR}"
PORT="${DAILY_RSYNC_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"
STATE_DIR="${DAILY_RSYNC_STATE_DIR:-$PROJECT_ROOT/data}"
LOG_FILE="$STATE_DIR/ui-server.log"
PID_FILE="$STATE_DIR/ui-server.pid"
NO_OPEN="${DAILY_RSYNC_NO_OPEN:-0}"

if [[ "$PORT" != <-> ]] || (( PORT < 1 || PORT > 65535 )); then
  print -u2 "잘못된 DAILY_RSYNC_PORT: $PORT"
  exit 2
fi

find_uv() {
  if [[ -n "${DAILY_RSYNC_UV:-}" && -x "$DAILY_RSYNC_UV" ]]; then
    print -r -- "$DAILY_RSYNC_UV"
    return 0
  fi
  local candidate
  for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [[ -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

healthy() {
  /usr/bin/curl -fsS --max-time 2 "$URL/api/status" >/dev/null 2>&1
}

managed_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid command
  pid="$(head -n 1 "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" == <-> ]] || return 1
  /bin/kill -0 "$pid" 2>/dev/null || return 1
  command="$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *"daily-rsync serve"* ]] || return 1
  print -r -- "$pid"
}

cleanup_stale_pid() {
  if [[ -f "$PID_FILE" ]] && ! managed_pid >/dev/null; then
    /bin/rm -f "$PID_FILE"
  fi
}

open_ui() {
  if [[ "$NO_OPEN" != "1" ]]; then
    /usr/bin/open "$URL"
  fi
}

start_server() {
  local pid uv
  if healthy; then
    if pid="$(managed_pid 2>/dev/null)"; then
      print "Daily Rsync가 이미 실행 중입니다. (PID $pid, $URL)"
      open_ui
      return 0
    fi
    print -u2 "${URL}에 PID로 관리되지 않는 서버가 응답합니다. 안전을 위해 새 서버를 시작하지 않습니다."
    return 1
  fi

  cleanup_stale_pid
  if ! uv="$(find_uv)"; then
    print -u2 "uv 실행 파일을 찾지 못했습니다. ~/.local/bin 또는 Homebrew 설치를 확인하세요."
    return 1
  fi
  if [[ ! -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    print -u2 "daily-rsync 프로젝트를 찾지 못했습니다: $PROJECT_ROOT"
    return 1
  fi

  /bin/mkdir -p "$STATE_DIR"
  /bin/chmod 700 "$STATE_DIR"
  cd "$PROJECT_ROOT"
  /usr/bin/nohup "$uv" run daily-rsync serve --no-open --port "$PORT" \
    >>"$LOG_FILE" 2>&1 </dev/null &
  pid=$!
  print -r -- "$pid" >"$PID_FILE"
  /bin/chmod 600 "$PID_FILE" "$LOG_FILE"

  local attempt
  for attempt in {1..80}; do
    if healthy; then
      print "Daily Rsync를 시작했습니다. (PID $pid, $URL)"
      open_ui
      return 0
    fi
    if ! /bin/kill -0 "$pid" 2>/dev/null; then
      /bin/rm -f "$PID_FILE"
      print -u2 "Daily Rsync가 시작 중 종료됐습니다. 로그: $LOG_FILE"
      return 1
    fi
    /bin/sleep 0.25
  done

  /bin/kill -TERM "$pid" 2>/dev/null || true
  /bin/rm -f "$PID_FILE"
  print -u2 "20초 안에 Daily Rsync가 준비되지 않았습니다. 로그: $LOG_FILE"
  return 1
}

stop_server() {
  local pid attempt
  if ! pid="$(managed_pid 2>/dev/null)"; then
    cleanup_stale_pid
    if healthy; then
      print -u2 "서버가 응답하지만 이 스크립트가 관리하는 PID가 아닙니다. 임의 종료하지 않습니다."
      return 1
    fi
    print "Daily Rsync는 이미 꺼져 있습니다."
    return 0
  fi

  /bin/kill -TERM "$pid"
  for attempt in {1..40}; do
    if ! /bin/kill -0 "$pid" 2>/dev/null; then
      /bin/rm -f "$PID_FILE"
      print "Daily Rsync를 종료했습니다. (PID $pid)"
      return 0
    fi
    /bin/sleep 0.25
  done

  print -u2 "10초 안에 종료되지 않았습니다. 강제 종료하지 않았습니다. (PID $pid)"
  return 1
}

show_status() {
  local pid
  if healthy; then
    if pid="$(managed_pid 2>/dev/null)"; then
      print "RUNNING pid=$pid url=$URL log=$LOG_FILE"
    else
      print "RUNNING unmanaged url=$URL"
    fi
    return 0
  fi
  cleanup_stale_pid
  print "STOPPED url=$URL"
  return 3
}

action="${1:-toggle}"
case "$action" in
  toggle)
    if healthy || managed_pid >/dev/null 2>&1; then
      stop_server
    else
      start_server
    fi
    ;;
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  restart)
    stop_server
    start_server
    ;;
  status)
    show_status
    ;;
  open)
    if ! healthy; then
      start_server
    else
      open_ui
    fi
    ;;
  *)
    print -u2 "사용법: $0 [toggle|start|stop|restart|status|open]"
    exit 2
    ;;
esac

#!/usr/bin/env bash
# Append-mode launcher for scripts/loop.sh.
#
# Keeps a single continuous log (.delegate/loop.log) across restarts by
# appending with a timestamped header instead of truncating, refuses to start
# a second instance, and records the pid for --status checks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$REPO_ROOT/.delegate/loop.log"
PID_FILE="$REPO_ROOT/.delegate/loop.pid"
LOCK_FILE="$REPO_ROOT/.delegate/loop.lock"

mkdir -p "$REPO_ROOT/.delegate"

if [ -f "$PID_FILE" ] && pid="$(cat "$PID_FILE" 2>/dev/null)" && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  echo "loop already running (pid $pid); use scripts/loop.sh --stop first" >&2
  exit 1
fi

# The loop takes an exclusive flock itself; catching it here gives a clearer error.
exec 9>>"$LOCK_FILE"
if ! flock -n 9; then
  echo "loop lock is held ($LOCK_FILE); another instance is running" >&2
  exit 1
fi
flock -u 9

{
  printf '\n=== %s loop-start (%s) ===\n' "$(date -Is)" "$*"
  exec >>"$LOG_FILE" 2>&1
}

exec "$SCRIPT_DIR/loop.sh" "$@"

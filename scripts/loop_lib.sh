# Shared helpers for scripts/loop.sh

STALL_SECONDS="${STALL_SECONDS:-900}"   # kill a relay run with no new events for this long
STALL_POLL="${STALL_POLL:-60}"

newest_activity_file() { # outdir -> newest file under outdir (prefers events.jsonl)
  local od="$1" f
  if [ -f "$od/events.jsonl" ]; then printf '%s' "$od/events.jsonl"; return 0; fi
  f="$(find "$od" -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"
  [ -n "$f" ] && printf '%s' "$f"
}

mtime_of() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }

run_relay_guarded() { # outdir -- <relay args...>
  # Runs `node $RELAY ...` and kills it if the run dir shows no activity for
  # STALL_SECONDS. The relay turns a SIGTERM into a terminated-child report, so
  # run_engineer/run_reviewer see a normal failed run and rotate the model.
  local outdir="$1"; shift
  local child monitor rc started now act mt last_mt="" last_change
  started="$(date +%s)"; last_change="$started"
  node "$RELAY" "$@" &
  child=$!
  (
    while kill -0 "$child" 2>/dev/null; do
      sleep "$STALL_POLL"
      now="$(date +%s)"
      act="$(newest_activity_file "$outdir")"
      if [ -z "$act" ]; then
        if [ $(( now - started )) -ge "$STALL_SECONDS" ]; then
          warn "relay produced no activity in $outdir within ${STALL_SECONDS}s; killing pid $child"
          touch "$outdir/.stalled"
          kill -TERM "$child" 2>/dev/null
          exit 0
        fi
        continue
      fi
      mt="$(mtime_of "$act")"
      if [ "$mt" != "$last_mt" ]; then last_mt="$mt"; last_change="$now"; continue; fi
      if [ $(( now - last_change )) -ge "$STALL_SECONDS" ]; then
        warn "relay stalled: no new events in $outdir for ${STALL_SECONDS}s; killing pid $child"
        touch "$outdir/.stalled"
        kill -TERM "$child" 2>/dev/null
        exit 0
      fi
    done
  ) &
  monitor=$!
  wait "$child"; rc=$?
  kill "$monitor" 2>/dev/null; wait "$monitor" 2>/dev/null
  return $rc
}

model_healthy() { # model -> 0 if usable
  [ "${MODEL_HEALTH[$1]:-unknown}" != "unhealthy" ]
}

mark_model_ok() {
  MODEL_HEALTH["$1"]=healthy
  MODEL_FAILS["$1"]=0
}

mark_model_bad() {
  local m="$1"
  local n="${MODEL_FAILS[$m]:-0}"
  n=$((n+1))
  MODEL_FAILS["$m"]=$n
  if [ "$n" -ge 2 ]; then
    MODEL_HEALTH["$m"]=unhealthy
    warn "model marked unhealthy: $m (failures=$n)"
  fi
}

gemini_tokens_used() {
  if [ -f "$GEMINI_USAGE_FILE" ]; then
    jq -r '.tokens // 0' "$GEMINI_USAGE_FILE" 2>/dev/null || echo 0
  else
    echo 0
  fi
}

gemini_tokens_remaining() {
  local used; used="$(gemini_tokens_used)"
  echo $(( GEMINI_TOKEN_CAP - used ))
}

record_gemini_usage() { # result.json
  local rj="$1" tok
  tok="$(jq -r '.usage.totalTokens // .tokens.total // .totalTokens // 0' "$rj" 2>/dev/null)"
  case "$tok" in ''|null) tok=0 ;; esac
  local used; used="$(gemini_tokens_used)"
  printf '{"tokens":%d}\n' "$((used + tok))" > "$GEMINI_USAGE_FILE"
}

next_free_model() { # current other -> next healthy free model
  local other="$1" m
  for m in "${ROTATION_MODELS[@]}"; do
    [ "$m" = "$other" ] && continue
    if model_healthy "$m"; then printf '%s' "$m"; return 0; fi
  done
  return 1
}

ensure_engineer_model() {
  if ! model_healthy "$CUR_ENGINEER"; then
    local nm
    if nm="$(next_free_model "$CUR_REVIEWER")"; then
      warn "engineer model unhealthy; switching to $nm"
      CUR_ENGINEER="$nm"
    elif [ "$(gemini_tokens_remaining)" -gt 0 ]; then
      warn "free engineer models exhausted; using Gemini engineer fallback"
      CUR_ENGINEER="$GEMINI_ENGINEER_MODEL"
    else
      die "no healthy engineer model and Gemini cap exhausted"
    fi
  fi
}

ensure_reviewer_model() {
  if ! model_healthy "$CUR_REVIEWER"; then
    local nm
    if nm="$(next_free_model "$CUR_ENGINEER")"; then
      warn "reviewer model unhealthy; switching to $nm"
      CUR_REVIEWER="$nm"
    elif [ "$(gemini_tokens_remaining)" -gt 0 ]; then
      warn "free reviewer models exhausted; using Gemini reviewer fallback"
      CUR_REVIEWER="$GEMINI_REVIEWER_MODEL"
    else
      die "no healthy reviewer model and Gemini cap exhausted"
    fi
  fi
}

check_resources() {
  local avail_kb
  avail_kb="$(df -P "$REPO_ROOT" | awk 'NR==2{print $4}')"
  if [ "${avail_kb:-0}" -lt 1048576 ]; then
    warn "low disk space: ${avail_kb}KB available"
    return 1
  fi
  return 0
}

heartbeat() { date +%s > "$HEARTBEAT_FILE" 2>/dev/null || true; }

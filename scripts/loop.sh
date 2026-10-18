#!/usr/bin/env bash
# Anees close-the-loop driver.
#
# Sequentially works every open issue: engineer (DeepSeek, lane `build`)
# implements, an independent reviewer (GLM, lane `review`) gates it, and the
# driver lands it only on APPROVE + green CI. Bounded by the guards documented
# in docs/loop-runbook.md. It never restarts itself.
#
# Read docs/loop-runbook.md before running. Use --dry-run first.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RELAY="/home/islam/.agents/skills/opencode-delegate/scripts/relay.mjs"
STATE_DIR="$REPO_ROOT/.delegate"
STATE_FILE="$STATE_DIR/loop-state.json"
RUNS_DIR="$STATE_DIR/runs"
STOP_FILE="$STATE_DIR/STOP"
CONFIG_FILE="$STATE_DIR/config.json"

REPO_SLUG="IslamAbdelslam/anees"
BASE_BRANCH="master"

# ---------------------------------------------------------------- guards ----
MAX_FIX_CYCLES="${MAX_FIX_CYCLES:-3}"
MAX_ISSUES="${MAX_ISSUES:-0}"              # 0 = all
MAX_WALL_SECONDS="${MAX_WALL_SECONDS:-43200}"
MAX_ENGINEER_RUNS="${MAX_ENGINEER_RUNS:-120}"
CI_TIMEOUT="${CI_TIMEOUT:-2700}"          # 45 min
ENGINEER_TIMEOUT="${ENGINEER_TIMEOUT:-2h}"
REVIEW_TIMEOUT="${REVIEW_TIMEOUT:-1h}"
ADMIN_MERGE="${LOOP_ADMIN_MERGE:-0}"

# runtime options
DRY_RUN=0
ONLY_ISSUE=""
DO_STOP=0
ONLY_ONCE=0

ORIG_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
START_EPOCH=$(date +%s)
ENGINEER_RUNS=0
REVIEWER_RUNS=0
LANDED=0

# Model continuity: when a lane's context window is exhausted (a run fails or a
# fresh model is needed), rotate to the next-best free coding model. Ordered by
# the coding-lane benchmarks researched in docs/loop-runbook.md.
ROTATION_MODELS=(${ROTATION_MODELS:-opencode/beam-free opencode/big-pickle})
MAX_ROTATIONS="${MAX_ROTATIONS:-2}"
CTX_ROTATE_PCT="${CTX_ROTATE_PCT:-95}"          # rotate before the window fills
DEFAULT_CONTEXT_WINDOW="${DEFAULT_CONTEXT_WINDOW:-1000000}"
CUR_ENGINEER=""
CUR_REVIEWER=""

log()  { printf '\033[1;36m[loop]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[loop]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[loop] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --dry-run            Print the plan (selection + guards) and exit.
  --only <N>           Work only issue #N (ignores sequential selection).
  --max-issues <N>     Land at most N issues, then stop cleanly.
  --stop               Write .delegate/STOP and exit (halts a running loop).
  -h, --help           This help.
Env dials: MAX_FIX_CYCLES MAX_ISSUES MAX_WALL_SECONDS MAX_ENGINEER_RUNS
           CI_TIMEOUT ENGINEER_TIMEOUT REVIEW_TIMEOUT LOOP_ADMIN_MERGE
           ROTATION_MODELS MAX_ROTATIONS CTX_ROTATE_PCT DEFAULT_CONTEXT_WINDOW
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --only)
      [ $# -ge 2 ] || die "--only needs a numeric issue number"
      case "$2" in ''|*[!0-9]*) die "--only needs a numeric issue number, got '$2'" ;; esac
      ONLY_ISSUE="$2"; shift ;;
    --max-issues)
      [ $# -ge 2 ] || die "--max-issues needs a number"
      case "$2" in ''|*[!0-9]*) die "--max-issues needs a number, got '$2'" ;; esac
      MAX_ISSUES="$2"; shift ;;
    --stop) DO_STOP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (use --help)" ;;
  esac
  shift
done

[ -f "$RELAY" ] || die "relay.mjs not found at $RELAY"
[ -f "$CONFIG_FILE" ] || die "lane config not found at $CONFIG_FILE (run delegate-setup first)"
command -v gh >/dev/null || die "gh CLI not found"
command -v jq >/dev/null || die "jq not found"
command -v node >/dev/null || die "node not found"

if [ "$DO_STOP" = 1 ]; then
  mkdir -p "$STATE_DIR"; : > "$STOP_FILE"
  log "stop switch set: $STOP_FILE"; exit 0
fi

# verify separation of duties: reviewer model must differ from engineer model
ENGINEER_MODEL="$(jq -r '.lanes.build.model // empty' "$CONFIG_FILE")"
REVIEWER_MODEL="$(jq -r '.lanes.review.model // empty' "$CONFIG_FILE")"
[ -n "$ENGINEER_MODEL" ] || die "lane 'build' has no model"
[ -n "$REVIEWER_MODEL" ] || die "lane 'review' has no model"
[ "$ENGINEER_MODEL" != "$REVIEWER_MODEL" ] || die "reviewer model must differ from engineer model"
CUR_ENGINEER="$ENGINEER_MODEL"
CUR_REVIEWER="$REVIEWER_MODEL"

mkdir -p "$RUNS_DIR"
[ -f "$STATE_FILE" ] || printf '{"issues":{}}\n' > "$STATE_FILE"

state_get() { jq -r --arg n "$1" --arg k "$2" '.issues[$n][$k] // empty' "$STATE_FILE" 2>/dev/null; }
state_set() {
  local n="$1" k="$2" v="$3" tmp
  tmp="$(mktemp)"
  jq --arg n "$n" --arg k "$k" --argjson v "$v" \
     '.issues[$n] = (.issues[$n] // {}) | .issues[$n][$k] = $v' \
     "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
}

# ------------------------------------------------------------ selection ----
gh_json() { # retry transient gh failures so a network blip can't fake a result
  local attempt=0
  while [ "$attempt" -lt 3 ]; do
    if "$@" 2>/dev/null; then return 0; fi
    attempt=$((attempt+1)); [ "$attempt" -lt 3 ] && sleep 3
  done
  return 1
}

all_open_issues() {
  gh_json gh issue list --repo "$REPO_SLUG" --state open --limit 200 \
     --json number --jq 'sort_by(.number) | .[].number'
}

issue_has_label() { # issue label
  gh issue view "$1" --repo "$REPO_SLUG" --json labels --jq '[.labels[].name]' 2>/dev/null \
    | jq -e --arg L "$2" 'index($L) != null' >/dev/null 2>&1
}

issue_is_escalated() {
  local n="$1"
  [ "$(state_get "$n" status)" = "escalated" ] && return 0
  issue_has_label "$n" loop:needs-human
}

SELECT_REASON="queue empty"
select_next() {
  SELECT_REASON="queue empty"
  local list n
  if ! list="$(all_open_issues)"; then
    SELECT_REASON="gh issue list failed after retries"
    warn "$SELECT_REASON"; return 1
  fi
  for n in $list; do
    if issue_has_label "$n" loop:pause; then
      SELECT_REASON="halted at paused issue #$n (loop:pause)"
      warn "$SELECT_REASON"; return 1
    fi
    if issue_is_escalated "$n"; then
      log "skipping #$n (escalated, terminal this run)"; continue
    fi
    printf '%s\n' "$n"; return 0
  done
  return 1
}

# ------------------------------------------------------ model rotation ------
context_window_for() { # model -> token window
  case "$1" in
    *nemotron-3-ultra*)          echo 262144 ;;
    *nemotron-3.5-lightning*)    echo 262144 ;;
    *nemotron-3.5-super-vl*)     echo 262144 ;;
    *glm*|*deepseek*)            echo 1000000 ;;
    *)                           echo "$DEFAULT_CONTEXT_WINDOW" ;;
  esac
}

ctx_pct() { # result.json -> integer percent of the context window used, or non-zero if unknown
  local rj="$1" ev tok ctx
  ev="$(jq -r '.eventsPath // empty' "$rj" 2>/dev/null)"
  [ -n "$ev" ] && [ -f "$ev" ] || return 1
  tok="$(jq -s '[.[] | select(.type=="step_finish")] | last | .part.tokens
                 | (.total // ((.input // 0) + ((.cache.read) // 0)))' "$ev" 2>/dev/null)"
  case "$tok" in ''|null|0) return 1 ;; esac
  ctx="$(context_window_for "$(jq -r '.model // empty' "$rj" 2>/dev/null)")"
  awk -v t="$tok" -v c="$ctx" 'BEGIN { if (c > 0) printf "%d", (t * 100) / c; else exit 1 }'
}

advance_model() { # current other -> next rotation model that is not `other`
  local cur="$1" other="$2" i=-1 idx=0 m n j cand
  n=${#ROTATION_MODELS[@]}
  for m in "${ROTATION_MODELS[@]}"; do
    [ "$m" = "$cur" ] && { i=$idx; break; }
    idx=$((idx+1))
  done
  for ((j=1; j<=n; j++)); do
    cand="${ROTATION_MODELS[$(((i+j)%n))]}"
    [ "$cand" != "$other" ] && { printf '%s' "$cand"; return 0; }
  done
  printf '%s' "$cur"
}

run_engineer() { # brief outdir sessionfile -> 0 if a run completed
  local brief="$1" outdir="$2" sfile="$3" attempt=0
  while :; do
    local -a a=(--brief "$brief" --lane build --model "$CUR_ENGINEER" --cd "$REPO_ROOT"
                 --timeout "$ENGINEER_TIMEOUT" --out-dir "$outdir")
    [ -s "$sfile" ] && a+=(--session "$(cat "$sfile")")
    node "$RELAY" "${a[@]}"
    ENGINEER_RUNS=$((ENGINEER_RUNS+1))
    if [ -f "$outdir/result.json" ] && [ "$(jq -r '.status // empty' "$outdir/result.json")" = "completed" ]; then
      jq -r '.sessionId // empty' "$outdir/result.json" > "$sfile"
      local pct
      if pct="$(ctx_pct "$outdir/result.json")" && [ -n "$pct" ] && [ "$pct" -ge "$CTX_ROTATE_PCT" ]; then
        warn "engineer context at ${pct}% (>= ${CTX_ROTATE_PCT}%); rotating to another free model + fresh context"
        CUR_ENGINEER="$(advance_model "$CUR_ENGINEER" "$CUR_REVIEWER")"
        : > "$sfile"
      fi
      return 0
    fi
    [ "$attempt" -ge "$MAX_ROTATIONS" ] && return 1
    warn "engineer run not completed; rotating model away from $CUR_ENGINEER (fresh context)"
    CUR_ENGINEER="$(advance_model "$CUR_ENGINEER" "$CUR_REVIEWER")"
    : > "$sfile"; attempt=$((attempt+1))
  done
}

run_reviewer() { # brief outdir verdictout -> 0 if a parseable verdict was produced
  local brief="$1" outdir="$2" vout="$3" attempt=0
  while :; do
    local -a a=(--brief "$brief" --lane review --model "$CUR_REVIEWER" --read-only
                --cd "$REPO_ROOT" --timeout "$REVIEW_TIMEOUT" --out-dir "$outdir")
    node "$RELAY" "${a[@]}"
    REVIEWER_RUNS=$((REVIEWER_RUNS+1))
    if [ -f "$outdir/result.json" ] && extract_verdict "$outdir/result.json" "$vout"; then
      local pct
      if pct="$(ctx_pct "$outdir/result.json")" && [ -n "$pct" ] && [ "$pct" -ge "$CTX_ROTATE_PCT" ]; then
        warn "reviewer context at ${pct}% (>= ${CTX_ROTATE_PCT}%); next review uses another free model"
        CUR_REVIEWER="$(advance_model "$CUR_REVIEWER" "$CUR_ENGINEER")"
      fi
      return 0
    fi
    [ "$attempt" -ge "$MAX_ROTATIONS" ] && return 1
    warn "reviewer verdict unavailable; rotating model away from $CUR_REVIEWER"
    CUR_REVIEWER="$(advance_model "$CUR_REVIEWER" "$CUR_ENGINEER")"
    attempt=$((attempt+1))
  done
}

# ------------------------------------------------------------------ gates ---
changed_files() {
  { git -C "$REPO_ROOT" diff --name-only "$BASE_BRANCH"...HEAD 2>/dev/null
    git -C "$REPO_ROOT" diff --name-only HEAD 2>/dev/null
    git -C "$REPO_ROOT" diff --name-only --cached 2>/dev/null; } | sort -u
}
touches_rust() { changed_files | grep -qE '(^|/)Cargo\.(toml|lock)$|\.rs$'; }
touches_python() { changed_files | grep -q '^python-backend/'; }

run_gates() { # -> writes output to $1, returns 0 if all required gates pass
  local out="$1" rc=0
  : > "$out"
  if touches_rust; then
    log "gate: rust (fmt + clippy + test)"
    { cargo fmt --all -- --check; }            >>"$out" 2>&1 || { rc=1; echo "RUST_FMT_FAILED" >>"$out"; }
    { cargo clippy --workspace --all-targets -- -D warnings; } >>"$out" 2>&1 || { rc=1; echo "RUST_CLIPPY_FAILED" >>"$out"; }
    { cargo test --workspace; }                >>"$out" 2>&1 || { rc=1; echo "RUST_TEST_FAILED" >>"$out"; }
  fi
  if touches_python; then
    log "gate: python (ruff + pytest)"
    { ruff check python-backend/; }            >>"$out" 2>&1 || { rc=1; echo "RUFF_CHECK_FAILED" >>"$out"; }
    { ruff format --check python-backend/; }   >>"$out" 2>&1 || { rc=1; echo "RUFF_FMT_FAILED" >>"$out"; }
    if [ -d python-backend/tests ]; then
      { pytest python-backend/tests; }         >>"$out" 2>&1 || { rc=1; echo "PYTEST_FAILED" >>"$out"; }
    fi
    if [ -f mypy.ini ] || [ -f python-backend/mypy.ini ] || grep -qs '\[tool.mypy\]' python-backend/pyproject.toml; then
      { mypy python-backend/; }                >>"$out" 2>&1 || { rc=1; echo "MYPY_FAILED" >>"$out"; }
    fi
  fi
  return $rc
}

diff_hash() { git -C "$REPO_ROOT" add -A >/dev/null 2>&1; git -C "$REPO_ROOT" diff --cached | sha256sum | cut -d' ' -f1; }
diff_lines() { git -C "$REPO_ROOT" diff --cached --numstat | awk '{a+=$1;d+=$2} END{print a+d+0}'; }

# --------------------------------------------------------------- briefs -----
make_engineer_brief() { # issue run_dir [feedback_file]
  local n="$1" dir="$2" feedback="${3:-}" title
  title="$(gh issue view "$n" --repo "$REPO_SLUG" --json title --jq .title)"
  {
    printf 'You are the engineer for the Anees repository (working root: %s).\n' "$REPO_ROOT"
    printf 'Implement GitHub issue #%s: %s\n\n' "$n" "$title"
    cat <<'EOF'
<issue_body>
EOF
    gh issue view "$n" --repo "$REPO_SLUG" --json body --jq .body
    cat <<'EOF'
</issue_body>
EOF
    if [ -n "$feedback" ] && [ -f "$feedback" ]; then
      printf '\n<previous_feedback>\n'
      printf 'A prior attempt was rejected or failed. Address every point below.\n'
      cat "$feedback"
      printf '\n</previous_feedback>\n'
    fi
    cat <<'EOF'

<repo_rules>
- Follow adr/contribution-adr.md and adr/fixing-issues-adr.md (read them).
- YAGNI: simplest solution meeting the acceptance criteria. No speculative abstraction.
- Do NOT commit; leave changes in the working tree.
- Keep a single issue's diff under ~500 changed lines; if larger, prefer the
  smallest correct subset and state what remains.
- Mirror AGENTS.md conventions and existing code style. No new comments unless required.
</repo_rules>

<gates_you_must_run>
Run the gates that apply and report raw results:
- cargo fmt --all -- --check
- cargo clippy --workspace --all-targets -- -D warnings
- cargo test --workspace
- ruff check python-backend/ ; ruff format --check python-backend/
- pytest python-backend/tests   (if the suite exists)
</gates_you_must_run>

<report_contract>
End your final message with these exact lines:
ACCEPTANCE_CRITERIA:
- <criterion 1> :: met|not_met :: <how verified>
...
GATES: <command> => pass|fail  (one per line)
COMMIT_MSG: <type(scope): subject>   # conventional commit subject for this issue
NOTES: <anything the reviewer must know; scope decisions; open questions>
</report_contract>
EOF
  } > "$dir/brief-engineer.txt"
}

make_review_brief() { # issue run_dir diff_file criteria_file
  local n="$1" dir="$2" difffile="$3" crit="$4" title
  title="$(gh issue view "$n" --repo "$REPO_SLUG" --json title --jq .title)"
  {
    printf 'You are an INDEPENDENT reviewer/tester for the Anees repository (root: %s).\n' "$REPO_ROOT"
    cat <<'EOF'
Do not trust the implementer. Verify against the code and by running the gates yourself.

EOF
    printf 'Issue #%s: %s\n' "$n" "$title"
    printf 'The acceptance criteria are in: %s\n' "$crit"
    printf 'The full diff under review is in: %s\n' "$difffile"
    cat <<'EOF'
You may also read any file in the repository, and run:
  cargo fmt --all -- --check ; cargo clippy --workspace --all-targets -- -D warnings ; cargo test --workspace
  ruff check python-backend/ ; ruff format --check python-backend/ ; pytest python-backend/tests

Judge adversarially: try to falsify each acceptance criterion. Check for scope
creep (YAGNI), dead code, missing tests, and >500-line diffs. Report only
findings you can substantiate.

End your final message with exactly one line:
VERDICT_JSON: {"verdict":"APPROVE"|"REQUEST_CHANGES","criteria":[{"text":"...","met":true}],"findings":[{"severity":"blocker|major|minor","file":"...","issue":"...","fix":"..."}],"evidence":{"commands":["..."]}}
APPROVE requires every criterion met, gates green, and non-empty evidence.commands.
EOF
  } > "$dir/brief-review.txt"
}

extract_verdict() { # result.json outfile -> 0 if valid json written
  local result="$1" out="$2"
  jq -r '.finalMessage // ""' "$result" 2>/dev/null \
    | grep -o 'VERDICT_JSON:.*' | tail -1 \
    | sed 's/^VERDICT_JSON:[[:space:]]*//' > "$out"
  jq -e . "$out" >/dev/null 2>&1
}

verdict_field() { jq -r "$2" "$1" 2>/dev/null; }

escalate() { # issue run_dir reason
  local n="$1" dir="$2" reason="$3"
  log "ESCALATING #$n: $reason"
  {
    echo "# Issue #$n escalated"; echo
    echo "Reason: $reason"; echo
    echo "## Last verdict"; echo '```json'
    [ -f "$dir/verdict.json" ] && cat "$dir/verdict.json" || echo '{}'
    echo '```'; echo
    echo "Human action needed: review $dir and decide the next step."
  } > "$dir/ESCALATION.md"
  gh label create loop:needs-human --repo "$REPO_SLUG" --color B60205 \
       --description "Loop stopped; needs a human" >/dev/null 2>&1 || true
  gh issue edit "$n" --repo "$REPO_SLUG" --add-label loop:needs-human >/dev/null 2>&1 || true
  state_set "$n" status '"escalated"'
  state_set "$n" reason "$(jq -Rn --arg r "$reason" '$r')"
}

wait_ci() { # pr [logfile] -> 0 green, 1 red/fail, 2 timeout
  local pr="$1" logf="${2:-}" waited=0 s
  [ -n "$logf" ] && : > "$logf"
  while [ "$waited" -lt "$CI_TIMEOUT" ]; do
    s="$(gh pr checks "$pr" --repo "$REPO_SLUG" 2>&1 || true)"
    [ -n "$logf" ] && printf '%s\n' "$s" >> "$logf"
    if [ -z "$s" ] || printf '%s' "$s" | grep -qiE 'no checks|no commit|not been'; then
      sleep 20; waited=$((waited+20)); continue
    fi
    printf '%s' "$s" | grep -qiE 'fail|not successful' && return 1
    printf '%s' "$s" | grep -qiE 'pending|in_progress|queued|running' && {
      sleep 20; waited=$((waited+20)); continue; }
    return 0
  done
  return 2
}

# ------------------------------------------------------------- iteration ----
work_issue() { # n -> 0 landed, 1 escalated
  local n="$1"
  local dir="$RUNS_DIR/$n"; mkdir -p "$dir"
  local crit="$dir/criteria.txt"
  gh issue view "$n" --repo "$REPO_SLUG" --json body --jq .body > "$crit"
  local title; title="$(gh issue view "$n" --repo "$REPO_SLUG" --json title --jq .title)"
  local slug; slug="$(printf '%s' "$title" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//' | cut -c1-40)"
  local branch="issue-$n-$slug"

  log "=== issue #$n: $title ==="
  git -C "$REPO_ROOT" checkout "$BASE_BRANCH" --quiet 2>/dev/null || die "cannot checkout $BASE_BRANCH"
  git -C "$REPO_ROOT" fetch origin "$BASE_BRANCH" --quiet || true
  git -C "$REPO_ROOT" reset --hard "origin/$BASE_BRANCH" --quiet
  if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    log "resuming existing branch $branch from origin (no history reset)"
    git -C "$REPO_ROOT" fetch origin "$branch" --quiet || true
    git -C "$REPO_ROOT" checkout -B "$branch" "origin/$branch" --quiet
  else
    git -C "$REPO_ROOT" checkout -B "$branch" "origin/$BASE_BRANCH" --quiet
  fi

  local cycle=0 prev_hash="" prev_sig=""
  while :; do
    # ---- guards ----
    [ -f "$STOP_FILE" ] && { warn "STOP file present; halting"; return 1; }
    if [ "$ENGINEER_RUNS" -ge "$MAX_ENGINEER_RUNS" ]; then warn "engineer budget hit"; return 1; fi
    if [ $(( $(date +%s) - START_EPOCH )) -ge "$MAX_WALL_SECONDS" ]; then warn "wall-clock budget hit"; return 1; fi
    [ "$cycle" -ge "$MAX_FIX_CYCLES" ] && { escalate "$n" "$dir" "max fix cycles ($MAX_FIX_CYCLES) reached"; return 1; }

    # ---- engineer (with prior feedback on fix cycles) ----
    local feedback=""
    if [ "$cycle" -gt 0 ]; then
      feedback="$dir/feedback-$cycle.txt"
      {
        echo "## Reviewer findings"
        jq -r '(.findings // [])[] | "- [\(.severity)] \(.file): \(.issue) => \(.fix)"' "$dir/verdict.json" 2>/dev/null || true
        echo
        if [ -f "$dir/review-$((cycle-1))/result.json" ]; then
          echo "## Reviewer report (tail)"
          jq -r '.finalMessage // ""' "$dir/review-$((cycle-1))/result.json" 2>/dev/null | tail -c 4000 || true
          echo
        fi
        if [ -f "$dir/gates-$((cycle-1)).txt" ]; then
          echo "## Last gate output (tail)"
          tail -40 "$dir/gates-$((cycle-1)).txt" || true
          echo
        fi
        if [ -f "$dir/ci-$((cycle-1)).txt" ]; then
          echo "## Last CI output (tail)"
          tail -40 "$dir/ci-$((cycle-1)).txt" || true
        fi
      } > "$feedback"
    fi
    make_engineer_brief "$n" "$dir" "$feedback"
    local edir="$dir/engineer-$cycle"; mkdir -p "$edir"
    if ! run_engineer "$dir/brief-engineer.txt" "$edir" "$dir/engineer-session.txt"; then
      escalate "$n" "$dir" "engineer failed after $MAX_ROTATIONS model rotation(s)"
      return 1
    fi

    # ---- progress guards ----
    local cur_hash; cur_hash="$(diff_hash)"
    if [ -n "$prev_hash" ] && [ "$cur_hash" = "$prev_hash" ]; then
      escalate "$n" "$dir" "identical diff across two cycles (no progress)"; return 1
    fi
    prev_hash="$cur_hash"
    local lines; lines="$(diff_lines)"
    if [ "$lines" -eq 0 ]; then escalate "$n" "$dir" "engineer produced an empty diff"; return 1; fi

    # ---- gates (driver, not self-report) ----
    local gateout="$dir/gates-$cycle.txt"
    if ! run_gates "$gateout"; then
      local sig; sig="$(grep -oE '^[A-Z_]+_FAILED$' "$gateout" | sort -u | sha256sum | cut -d' ' -f1)"
      if [ -n "$prev_sig" ] && [ "$sig" = "$prev_sig" ]; then
        escalate "$n" "$dir" "same gate failure twice; no progress"; return 1
      fi
      prev_sig="$sig"
      warn "gates failed (cycle $cycle); sending back to engineer"
      cycle=$((cycle+1)); continue
    fi

    # ---- commit + push + PR ----
    local msg; msg="$(jq -r '.finalMessage // ""' "$edir/result.json" | grep -o 'COMMIT_MSG:.*' | head -1 | sed 's/^COMMIT_MSG:[[:space:]]*//')"
    [ -n "$msg" ] || msg="chore(issue-$n): $title"
    git -C "$REPO_ROOT" add -A
    git -C "$REPO_ROOT" commit -q -m "$msg

Refs #$n"
    if ! git -C "$REPO_ROOT" push -u origin "$branch" --quiet; then
      escalate "$n" "$dir" "git push failed for branch $branch (refusing to review an unpushed head)"
      return 1
    fi

    local pr; pr="$(gh pr view "$branch" --repo "$REPO_SLUG" --json number --jq .number 2>/dev/null)"
    if [ -z "$pr" ]; then
      gh pr create --repo "$REPO_SLUG" --base "$BASE_BRANCH" --head "$branch" \
        --title "[#$n] $title" \
        --body "Fixes #$n

Automated by scripts/loop.sh (engineer: $ENGINEER_MODEL; reviewer: $REVIEWER_MODEL).
Diff: $lines changed lines (cycle $cycle)." >/dev/null
      pr="$(gh pr view "$branch" --repo "$REPO_SLUG" --json number --jq .number)"
    fi
    log "#$n PR #$pr (cycle $cycle, $lines lines)"

    git -C "$REPO_ROOT" diff "$BASE_BRANCH"...HEAD > "$dir/diff-$cycle.patch"

    # ---- independent review ----
    make_review_brief "$n" "$dir" "$dir/diff-$cycle.patch" "$crit"
    local rdir="$dir/review-$cycle"; mkdir -p "$rdir"
    if ! run_reviewer "$dir/brief-review.txt" "$rdir" "$dir/verdict.json"; then
      escalate "$n" "$dir" "reviewer produced no parseable verdict after $MAX_ROTATIONS rotation(s)"
      return 1
    fi
    local verdict reviewer_used
    verdict="$(verdict_field "$dir/verdict.json" .verdict)"
    reviewer_used="$(jq -r '.model // empty' "$rdir/result.json" 2>/dev/null)"
    [ -n "$reviewer_used" ] || reviewer_used="$CUR_REVIEWER"
    issue_comment "$n" "Review (cycle $cycle): **$verdict** by $reviewer_used. $(jq -c '.findings // []' "$dir/verdict.json")"

    if [ "$verdict" != "APPROVE" ]; then
      warn "review: $verdict (cycle $cycle)"
      cycle=$((cycle+1)); continue
    fi
    # evidence check: APPROVE must carry commands
    if [ "$(jq -r '(.evidence.commands // []) | length' "$dir/verdict.json")" -eq 0 ]; then
      warn "APPROVE without evidence; requiring one re-review"
      cycle=$((cycle+1)); continue
    fi

    # ---- CI + merge ----
    log "waiting for CI on PR #$pr"
    wait_ci "$pr" "$dir/ci-$cycle.txt"; local ci=$?
    if [ "$ci" -eq 1 ]; then warn "CI red on #$n"; cycle=$((cycle+1)); continue; fi
    if [ "$ci" -eq 2 ]; then escalate "$n" "$dir" "CI timed out"; return 1; fi

    local mflag=""
    [ "$ADMIN_MERGE" = "1" ] && mflag="--admin"
    # shellcheck disable=SC2086
    gh pr merge "$pr" --repo "$REPO_SLUG" --squash $mflag \
      --body "Squash-merge #$n (engineer: $ENGINEER_MODEL, review: $REVIEWER_MODEL)." >/dev/null \
      || { escalate "$n" "$dir" "auto-merge failed"; return 1; }

    # ensure the issue is closed (Fixes #N should close it; fall back if not)
    if [ "$(gh issue view "$n" --repo "$REPO_SLUG" --json state --jq .state 2>/dev/null)" != "CLOSED" ]; then
      gh issue close "$n" --repo "$REPO_SLUG" \
        --comment "Landed via PR #$pr (auto-closed as a fallback)." >/dev/null 2>&1 || true
    fi

    log "LANDED #$n via PR #$pr"
    state_set "$n" status '"merged"'
    state_set "$n" pr "$pr"
    LANDED=$((LANDED+1))
    return 0
  done
}

issue_comment() {
  gh issue comment "$1" --repo "$REPO_SLUG" --body "$2" >/dev/null 2>&1 || true
}

# ------------------------------------------------------------------ main ----
[ "$DRY_RUN" = 1 ] && {
  log "dry-run: engineer=$ENGINEER_MODEL reviewer=$REVIEWER_MODEL"
  log "guards: max_fix_cycles=$MAX_FIX_CYCLES max_issues=$MAX_ISSUES wall=${MAX_WALL_SECONDS}s engineer_runs=$MAX_ENGINEER_RUNS"
  log "rotation: models=${ROTATION_MODELS[*]} rotate_at>=${CTX_ROTATE_PCT}% max_rotations=$MAX_ROTATIONS"
  if [ -n "$ONLY_ISSUE" ]; then log "would work issue #$ONLY_ISSUE"
  else
    n="$(select_next)" && log "next issue: #$n" || log "no eligible open issue"
  fi
  exit 0
}

[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree dirty; commit or stash first"

while :; do
  [ -f "$STOP_FILE" ] && { warn "STOP file present; halting"; break; }
  [ "$MAX_ISSUES" -gt 0 ] && [ "$LANDED" -ge "$MAX_ISSUES" ] && { log "max-issues ($MAX_ISSUES) reached"; break; }
  if [ $(( $(date +%s) - START_EPOCH )) -ge "$MAX_WALL_SECONDS" ]; then warn "wall-clock budget reached"; break; fi
  if [ "$ENGINEER_RUNS" -ge "$MAX_ENGINEER_RUNS" ]; then warn "engineer budget reached"; break; fi

  if [ -n "$ONLY_ISSUE" ]; then
    N="$ONLY_ISSUE"; ONLY_ISSUE=""; ONLY_ONCE=1
  else
    N="$(select_next)" || { log "${SELECT_REASON:-queue empty}; done"; break; }
  fi

  if work_issue "$N"; then :; else warn "issue #$N not landed (continued)"; fi
  [ "$ONLY_ONCE" = 1 ] && { log "--only was set; stopping after issue #$N"; break; }
done

# restore caller's branch if it still exists
git -C "$REPO_ROOT" checkout "$ORIG_BRANCH" --quiet 2>/dev/null || true

log "finished: landed=$LANDED engineer_runs=$ENGINEER_RUNS reviewer_runs=$REVIEWER_RUNS state=$STATE_FILE"
[ -f "$STOP_FILE" ] && log "STOP switch is set at $STOP_FILE (remove it to resume)"
exit 0

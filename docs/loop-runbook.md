# Anees Close-The-Loop Runbook

**Status**: Active
**Date**: 2026-10-18
**Roles**: DeepSeek 4.1 Flash = engineer · GLM 5.3 Flash = independent reviewer/tester

This runbook operationalizes [contribution-adr.md](../adr/contribution-adr.md)
and [fixing-issues-adr.md](../adr/fixing-issues-adr.md) into a bounded,
resumable loop that works every open issue until the queue is empty or every
remaining issue is explicitly escalated.

It exists to answer one question: **how do we finish all issues without the
loop running forever or degrading into rubber-stamped, self-approved code?**

---

## 1. Roles and separation of duties

| Seat | Who | Model | Can edit tree? |
|---|---|---|---|
| Orchestrator | the driver (`scripts/loop.sh`) | — | git/gh only |
| Engineer | OpenCode `build` agent, lane `build` | `opencode/deepseek-v4.1-flash-free` | yes |
| Reviewer / Tester | OpenCode `plan` agent, lane `review` | `opencode/glm-5.3-flash-free` (variant high) | no (read-only) |

The reviewer **never** shares a session, context, or model with the engineer.
Separation of duties is what makes the approve/merge gate meaningful.

## 2. The per-issue loop

1. **Select** the lowest open issue whose lower-numbered siblings are all
   closed or `loop:needs-human`. Never skip ahead past an open, unescalated
   issue (ADR sequential rule).
2. **Branch** `issue-<N>-<slug>` off a freshly fetched `master`.
3. **Implement** — engineer writes code + tests only; it does **not** commit.
4. **Gate (driver)** — the driver re-runs the project gates itself. Never trust
   the engineer's "gates passed" claim.
5. **Commit** — the driver commits (Conventional Commit + `Refs #N`).
6. **Push + PR** — title `[#N] <summary>`, body carries `Fixes #N`.
7. **Independent review (GLM)** — reads the raw diff and the acceptance
   criteria, re-runs the gates, returns a JSON verdict. It does not see the
   engineer's summary.
8. **Decide**:
   - APPROVE + CI green on the exact head → **squash-merge**, close the issue.
   - REQUEST_CHANGES or red CI → feed findings back, fix cycle +1, loop to step 4.
9. **Record** the outcome in `.delegate/loop-state.json`, then select the next
   issue.

## 3. Definition of Done (all must hold)

- Every acceptance criterion in the issue body is met.
- **Tests ship in the same PR** (ADR Rule 7): normal unit/integration tests for
  new logic, plus e2e coverage for any user-facing flow changed (the 5-step
  wizard), with CI wired to run any new suite.
- `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo test --workspace` — green (when Rust is touched).
- `ruff check python-backend/`, `ruff format --check python-backend/`,
  `mypy python-backend/`, `pytest python-backend/tests` — green (when Python is touched).
- Diff obeys ADR scope: YAGNI, and >500 changed lines is flagged for splitting.
- Reviewer verdict is APPROVE **with evidence** (commands actually run).
- PR CI green on the exact head commit.

## 3B. Model continuity & context-window rotation

A lane can hit its context window mid-issue. A **continuity keeper** is built
into the driver so the loop never stalls on that.

**Researched free-tier coding models** (coding-lane rankings, Oct 2026 — see
[opencode.ai/data](https://opencode.ai/data/compare/deepseek/glm),
[benchlm.ai](https://benchlm.ai/compare/deepseek-v4-1-flash-vs-glm-5),
[vals.ai](https://www.vals.ai/models/deepseek_deepseek-v4.1-flash),
[benchlm.ai/models/nemotron-3-ultra](https://benchlm.ai/models/nemotron-3-ultra)):

| Model (free) | Coding signal | Context |
|---|---|---|
| `opencode/deepseek-v4.1-flash-free` | coding lane 60.7 (#18/150); #1 open-weight on code migration | 1M |
| `opencode/glm-5.3-flash-free` | strong, cheap; 1M ctx | 1M |
| `opencode/nemotron-3-ultra-free` | SWE-bench Verified 71.9%, LiveCodeBench v6 89% | ~262K |

Default rotation pool: `ROTATION_MODELS="opencode/deepseek-v4.1-flash-free
opencode/glm-5.3-flash-free opencode/nemotron-3-ultra-free"`.

**Two triggers rotate to the next free model:**

1. **Run did not complete** (failure / timeout / unavailable): rotate and start
   a **fresh session** (no `--session`), up to `MAX_ROTATIONS` (default 2).
2. **Context window ≥ 95%** (`CTX_ROTATE_PCT`, default 95): after a run, the
   driver reads the last `step_finish` token total from the run's `events.jsonl`
   and divides by the model's window (`context_window_for`). At or above the
   threshold it rotates to another free model and clears the engineer session,
   so the next run starts with an empty window. This is proactive — the switch
   happens **before** the window fills.

**Separation is preserved:** `advance_model` never returns the model the other
lane is currently using, so the engineer and reviewer never converge on one
model.

Context windows are mapped per model (`context_window_for`); override the
unknown-model default with `DEFAULT_CONTEXT_WINDOW`.

---

## 4. Anti-infinite-loop guards

These are the reason this loop terminates. Every one is enforced in the driver.

| Guard | Default | Effect when hit |
|---|---|---|
| `MAX_FIX_CYCLES` | 3 | label issue `loop:needs-human`, escalate, move on |
| No-progress detector | on | identical (diff-hash + failing-gate signature) twice → escalate |
| Identical-diff detector | on | engineer returns an unchanged diff twice → escalate |
| Reviewer evidence check | on | APPROVE with no commands/evidence → one re-review, then escalate |
| Reviewer model equality | on | reviewer model == engineer model → refuse to run |
| `MAX_ISSUES` | 0 (all) | stop cleanly after N landed issues |
| `MAX_WALL_SECONDS` | 43200 (12h) | stop cleanly |
| `MAX_ENGINEER_RUNS` | 120 | stop cleanly |
| `MAX_ROTATIONS` | 2 | stop rotating; escalate the issue |
| `CTX_ROTATE_PCT` | 95 | rotate to another free model + fresh context |
| Stall watchdog | `STALL_SECONDS`=900 | relay run with no new events for 15 min → killed (SIGTERM → model marked bad → rotation) |
| Clean-tree per issue | on | dirty tree at issue start (incl. untracked) → refuse to start; no `git add -A` sweep |
| Self-tamper guard | on | loop scripts modified mid-run → refuse to commit (`LOOP_SELF_EDIT_OK=1` opts out) |
| Numeric selection guard | on | `select_next` returning a non-numeric id → halt (state-file junk prevention) |
| Kill switch | — | `.delegate/STOP` file present → stop before next dispatch |
| Escalation is terminal | on | a `loop:needs-human` issue is never re-picked in the same run |

Stale state is reconciled with GitHub: if the state file says `escalated` but
the issue is closed on GitHub, the driver rewrites the entry to `merged` instead
of skipping the issue.

**Termination condition**: the queue is empty, OR every remaining issue is
escalated (`loop:needs-human`) / blocked, OR a global budget is exhausted. The
driver prints a final report and exits — it does not restart itself.

## 5. Shared-state permission grant

Per `AGENTS.md`, push/merge normally require explicit per-operation approval.
For this loop the maintainer granted **standing auto-merge** (2026-10-18):
push feature branches, open PRs, and squash-merge **only** when review-approved
and CI is green.

Scope and limits of the grant:

- **Recorded at**: maintainer confirmation on PR #49 (2026-10-18). This
  runbook is the operative copy; the PR comment is the audit trail.
- **Revocable at any time**: run `gh issue edit <N> --add-label loop:pause`,
  create `.delegate/STOP`, or just stop the process. Revocation is immediate.
- **No remote branch deletion.** The grant covers merge only; the driver leaves
  branches in place (`gh pr merge --squash` without `--delete-branch`), because
  ADR Rule 6 reserves remote-branch deletion for maintainer confirmation.
- **No `--admin` force-merge** unless `LOOP_ADMIN_MERGE=1` is set explicitly.
- Merges happen **only** on APPROVE + green CI; a failed gate never merges.

## 6. Running it

Always start via the append-mode launcher so restarts keep one continuous log
(raw `> ...log` truncates the previous run's history):

```bash
# Validate without touching anything (recommended first):
scripts/loop.sh --dry-run

# Start (appends to .delegate/loop.log with a timestamped header):
scripts/loop-start.sh                      # full sequential queue
scripts/loop-start.sh --max-issues 5       # bounded pilot
scripts/loop-start.sh --only 7             # single issue

# Stop the loop:
scripts/loop.sh --stop        # writes .delegate/STOP

# Monitor:
tail -f .delegate/loop.log
```

**Operator rule**: never edit the repository while the loop is running — the
driver owns the tree. The per-issue clean-tree guard and the self-tamper guard
will refuse to start/commit if you do, but don't rely on the guard rails; use a
separate checkout for parallel work.

State and artifacts land under `.delegate/`:

```
.delegate/
  config.json        # lane map (build=deepseek, review=glm)
  loop-state.json    # per-issue status, cycles, counters (resumable)
  loop.log           # continuous log across restarts (append-only)
  loop.pid           # pid of the running loop (launcher also checks it)
  STOP               # present => loop halts before next dispatch
  runs/<N>/...       # briefs, result.json, diffs, verdicts per iteration
```

## 7. Escalation format

When an issue is escalated the driver writes `.delegate/runs/<N>/ESCALATION.md`
(reason, last verdict, last gate-output tail, last engineer run status incl. a
stall-watchdog marker when applicable) and labels the issue `loop:needs-human`:

```markdown
# Issue #<N> escalated
Reason: max fix cycles / no progress / identical diff / red CI / reviewer unavailable
Last verdict: <verdict json>
Last gate output (tail of .../gates-<k>.txt): ...
Engineer run (.../engineer-<k>): {"status": ..., "finalMessage": ...}
Human action needed: <what to decide>
```

## 8. Maturity definition

The app is "matured" for this loop when: all 36 issues are closed-merged or
escalated with a written rationale; the ADR gates are green on `master`; and a
final GLM end-to-end pass over the 5-step wizard (upload → config → process →
review → audio) is recorded in `docs/loop-report.md`.

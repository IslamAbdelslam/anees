# Fixing-Issues Workflow for Anees

## Architecture Decision Record: How Every Issue Gets Fixed

**Status**: Accepted  
**Date**: 2026-10-16  
**Deciders**: Project Maintainer + Orchestrator (muse-spark-1.3)

This ADR extends [contribution-adr.md](./contribution-adr.md) (sequential issues,
one branch per issue, PR required, review loop, YAGNI, ≤500-line commits).
It makes the day-to-day loop explicit and records the tech-stack research
that backs our implementation choices.

---

## Decision

### Rule 1. Research before fixing
- **Rule**: Before touching code for an issue, search the official docs,
  changelogs, and current best practices for every stack component the issue
  touches. Record version deltas and decisions in the Tech Notes below.
- **Rationale**: Prevents fixes based on stale knowledge (e.g. guessing at
  framework APIs), catches breaking changes early, and turns each issue into
  verified, citable decisions instead of opinions.

### Rule 2. The issue loop
- **Rule**: Every issue follows the same loop, no shortcuts:
  1. `git checkout master && git pull && git checkout -b issue-<N>-<slug>`
  2. Implement in small commits (Rule 4), verifying locally as you go
  3. Push and open PR `[#<N>] <summary>`; delegate review to the `review`
     lane (NVIDIA model) — see Rule 3
  4. Verify each reviewer comment against the code; fix what is true,
     with evidence, same as the code was verified
  5. Merge only when review-approved **and** CI is green; then start N+1
- **Rationale**: A fixed loop compounds quality the way sequential issues
  compound scope. Skipped steps are how deadlocks and red CI reach `master`.

### Rule 3. Independent review gate
- **Rule**: The coder (muse-spark-1.3, main seat) never self-approves.
  Every finished issue is reviewed through the `review` lane
  (`opencode/nemotron-3-ultra-free`, variant high) before merge.
- **Lane map** (repo scope, `.delegate/config.json`):
  - `review` → opencode / nemotron-3-ultra-free — post-issue code review
  - `build` → opencode / muse-spark-1.3-contributor-free — background coding
- **Rationale**: A second model catches what the implementer is blind to
  (proven on PR #41: 10/10 comments true; PR #42: invalid `hashFiles` usage).
  Free-tier reviewers keep main-seat spend untouched.

### Rule 4. Green-commit discipline
- **Rule**: Each commit is independently verifiable and ≤500 changed lines:
  - Rust: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`
  - Python: `ruff check`, `ruff format --check`, `pytest` where a suite exists
  - After pushing, watch the PR checks to green; never assume
- **Rationale**: Mirrors CI exactly, so "works on my machine" means
  "passes on the runner". Small commits make NVIDIA review effective.

### Rule 5. Scope discipline (YAGNI for dependencies)
- **Rule**: Do not upgrade dependencies inside a feature/fix issue.
  Research may flag a newer version — record it in Tech Notes and schedule
  it as a separate chore (dependency updates may run in parallel per
  contribution-adr.md exceptions).
- **Rationale**: Upgrades (e.g. eframe 0.29→0.36 with breaking API changes)
  are their own risk surface and must not ride along with unrelated work.

### Rule 6. Merge, retarget, and branch cleanup
- **Rule**: A PR merges to `master` only when review-approved **and** CI is
  green on its exact head commit. After landing:
  1. Sync `master` and verify the commits are present.
  2. Retarget any stacked PRs to `master` (this also turns CI on, since
     the workflow only runs on master-targeted PRs).
  3. Delete merged branches **only** after `git branch --merged` proves
     every commit is contained in `master` — and confirm with the
     maintainer before deleting *remote* branches.
  4. The PR body must carry `Fixes #N` for **every** issue in its diff
     (a PR that bundles issues must name and close all of them).
- **Rationale**: Unmerged stacks silently disable CI and hide scope;
  unverified deletions risk losing work. The confirmation step exists
  because remote history is shared (lesson: Oct 2026 cleanup).

### Rule 7. Tests are part of every change
- **Rule**: Every issue ships tests in the same PR — no exceptions:
  - **Normal tests** (unit + integration) for the logic it adds, in the
    language touched: `cargo test` for Rust, `pytest` for Python. Cover the
    happy path and at least one failure/edge path of new code.
  - **End-to-end tests** for any user-facing flow it changes. The canonical
    e2e suite drives the 5-step wizard — PDF upload → language/OCR config →
    chunked processing → text review → audio generation — and asserts the
    observable outcome (files produced, IPC contract, state transitions).
    GUI flows use an egui driver (e.g. `egui_kittest`/`egui_test`) with
    deterministic fixtures; backend-only issues satisfy e2e with a full
    pipeline run on a sample PDF (text, scanned, and mixed).
  - **Gate wiring is part of the issue**: if a new test suite or e2e harness
    is introduced, the same PR updates `.github/workflows/ci.yml` so the
    suite runs in CI. A test that never runs is not a test.
  - **Definition of Done includes tests**: the reviewer (Rule 3) MUST
    `REQUEST_CHANGES` on any issue whose new behaviour is untested, or whose
    user-facing change has no e2e coverage.
- **Rationale**: This is a maturing product with a long issue queue; tests are
  the only thing that lets fixes compound without regressions. Front-loading
  unit + e2e coverage (Issue #35) keeps the loop's auto-merge gate meaningful —
  green CI must actually mean "verified", not "compiled". E2e coverage is
  tracked explicitly so the 5-step wizard keeps working as screens land
  (Issues #18–#30).

---

## Tech Notes (researched 2026-10-16)

### Pinned vs latest

| Component | Pinned | Latest (2026-10) | Decision |
|---|---|---|---|
| eframe / egui | 0.29 | 0.36.2 (0.37 drafted) | **Stay on 0.29.** 0.30+ has breaking changes (`App::update`→`App::ui`, `Context::run`→`run_ui`, MSRV 1.95). GUI is scaffolding until issues #4/#18+; upgrade there. |
| tokio / serde / clap | 1.x / 1.x / 4.x | 1.x (current) | No action. |
| pydantic | ≥2.0 | v2 (current) | Use `Literal["2.0"]` for protocol version pinning, `model_validate_json` for wire parsing (faster, JSON-aware strictness). |
| ruff | ≥0.1 (CI installs latest; 0.17 locally) | 0.17+ | 0.17 default rules flag UP/I/BLE/SIM/ASYNC/C/TRY — code must satisfy them. Prefer `logger.exception` over `logger.error` in `except` blocks (satisfies BLE001 **and** captures tracebacks). |
| actions/checkout, setup-python | v4 / v5 | v7 / v7 | **Chore, not this issue.** v4/v5 still work; bump + `cache: pip` + SHA-pinning as a separate dependency chore. |
| camel-tools / fasttext / onnxruntime | ≥1.2 / ≥0.9 / ≥1.18 | current majors unchanged | No action. Lazy-load heavy models (established PR #41 fix). |

### IPC best practices applied (from jsonrpc.org spec + NDJSON practice)
- Full error-code table enforced on the wire: `-32700` parse error,
  `-32600` invalid request, `-32601` method not found, `-32602` invalid
  params, `-32603` internal error, `-32000…-32099` server range.
- Server MUST NOT reply to notifications (no `id` → no response);
  `id: null` only for unknown-id errors; `jsonrpc` MUST be exactly `"2.0"`.
- Transport stays NDJSON over stdio (one JSON object per `\n`, UTF-8) —
  matches tokio docs and industry practice (Rust↔Python sidecars).
- Reader/writer halves must be independently owned; a shared
  `Mutex<Transport>` held across `read_buf().await` deadlocks startup
  (proven on PR #41).
- `IpcClient` is internally synchronized (`Arc<Mutex<pending>>` inside);
  an outer `Mutex<IpcClient>` held across `call()` deadlocks for the same
  reason (proven on Issue #3: ping health check hung). Share bare `Arc`.

---

*This ADR is effective immediately and applies to Issue #2 onward.*

# Contribution Guidelines for Anees

## Architecture Decision Record: Contribution Workflow

**Status**: Accepted  
**Date**: 2026-10-15  
**Deciders**: Project Maintainers

---

## Decision

We adopt the following contribution workflow for all changes to the Anees project:

### 1. Sequential Issue Execution
- **Rule**: Start working on an issue only after the previous issue (by number) has been merged to `main`.
- **Rationale**: Ensures dependencies between issues are respected (e.g., IPC protocol must work before Python backend can use it). Prevents integration conflicts and keeps the codebase stable.

### 2. One Branch Per Issue
- **Rule**: Each issue gets its own feature branch named `issue-<number>-<short-description>`.
- **Examples**:
  - `issue-2-json-rpc-protocol`
  - `issue-18-welcome-screen-hero`
  - `issue-31-persistence-layer`
- **Rationale**: Isolates changes, makes code review focused, enables clean reverts, and keeps history readable.

### 3. Pull Request Required
- **Rule**: Every issue must be submitted as a Pull Request (PR) targeting `main`. Direct pushes to `main` are prohibited.
- **Rationale**: Enforces code review, runs CI checks, maintains audit trail, and allows discussion before merging.

### 4. Review Feedback Loop
- **Rule**: Before starting a new issue, the contributor must:
  1. Address all review comments on their previous PR
  2. Wait for approval and merge of the previous PR
  3. Pull latest `main` before creating the next branch
- **Rationale**: Prevents cascading errors, ensures quality compounds, and respects reviewer time.

### 5. Avoid Over-Engineering
- **Rule**: Implement the simplest solution that satisfies the acceptance criteria. No premature abstractions, no speculative features, no "future-proofing" beyond what the issue requires.
- **Guidelines**:
  - YAGNI (You Aren't Gonna Need It) applies
  - Prefer composition over inheritance
  - Avoid generic types until concretely needed
  - Hardcode configs until they need to be user-facing
  - Refactor when duplication hits 3×, not before
- **Rationale**: Keeps velocity high, reduces cognitive load, minimizes technical debt from unused code.

---

## Workflow Summary

```
Issue N-1 merged → Pull main → Create branch for Issue N
                                    ↓
                              Implement + Test
                                    ↓
                              Open PR → Code Review
                                    ↓
                              Address Comments → Approve → Merge
                                    ↓
                              (Now start Issue N+1)
```

---

## Branch Naming Convention

| Pattern | Example |
|---------|---------|
| `issue-<num>-<kebab-case-title>` | `issue-2-json-rpc-protocol` |
| `fix-<num>-<description>` | `fix-15-ocr-arabic-confidence` |
| `docs-<num>-<description>` | `docs-35-api-documentation` |

## PR Title Convention

| Pattern | Example |
|---------|---------|
| `[#<num>] <Imperative summary>` | `[#2] Implement JSON-RPC 2.0 protocol` |
| `Fix: <description> (#<num>)` | `Fix: Tesseract Arabic confidence threshold (#15)` |

## Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

Fixes #<issue-number>
```

**Types**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`

---

## Enforcement

- CI will fail if PR title doesn't match convention
- CI will fail if branch name doesn't match convention
- Maintainers will not review PRs for Issue N if Issue N-1 is not merged
- Over-engineered solutions will be rejected in review with "YAGNI" label

---

## Exceptions

Exceptions require maintainer approval via issue discussion before starting work:
- Security hotfixes (bypass sequence)
- Documentation-only changes (can run in parallel)
- Dependency updates (can run in parallel if non-breaking)

---

*This ADR is effective immediately for all contributors.*
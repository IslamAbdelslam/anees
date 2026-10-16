# Agent Instructions

Standing orders for any AI agent working in this repository. These override
convenience or momentum — when in doubt, stop and ask.

## 1. Never delete or push anything without the user's explicit permission first

Destructive or shared-state operations always require an explicit user
go-ahead, every time — a prior approval does not carry over:

- No `git push` to any branch or remote (including new branches and tags).
- No branch deletion, local (`git branch -d` / `-D`) or remote
  (`git push origin --delete`).
- No deleting tracked files or wiping untracked files (`git clean`, `rm`)
  beyond what the current task strictly requires.
- No merging pull requests, no force-push, no retargeting a PR's base
  branch, no closing/reopening issues or PRs.

Read-only operations (status, diff, log, fetch, CI checks, opening a PR
in draft for review) never need permission. Anything that changes shared
state does.

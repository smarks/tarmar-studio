# Agent Instructions

This project uses **GitHub issues** for task tracking (via the `gh` CLI).

## Quick Reference

```bash
gh issue list                          # All open issues
gh issue list --assignee @me           # Issues assigned to you
gh issue view <num>                    # View issue details
gh issue edit <num> --add-assignee @me # Claim work
gh issue comment <num> --body "..."    # Add progress notes
gh issue close <num>                   # Complete work
gh issue create --title "..." --body "..." --label enhancement
```

> The legacy `bd` (beads) tracker and the on-disk `.beads/` directory may still
> be present in the repo for historical reasons, but GitHub issues are now the
> source of truth. Do not create new tasks in `bd`.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** — `gh issue create` for anything that needs follow-up.
2. **Run quality gates** (if code changed) — tests, linters, builds.
3. **Update issue status** — close finished work (`gh issue close`), comment on in-progress items (`gh issue comment`).
4. **PUSH TO REMOTE** — this is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** — clear stashes, prune remote branches.
6. **Verify** — all changes committed AND pushed.
7. **Hand off** — provide context for next session.

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds.
- NEVER stop before pushing — that leaves work stranded locally.
- NEVER say "ready to push when you are" — YOU must push.
- If push fails, resolve and retry until it succeeds.

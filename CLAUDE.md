# Instructions for Claude Code sessions on this project

This project is actively worked on from two machines (a Windows Dell and a Mac
mini). This file is auto-loaded by Claude Code for any session opened in this
directory, on either machine — read it before doing anything else.

## Git: sync early, sync often

This overrides the general default of "only commit when explicitly asked." For
this repo specifically, commit and push are pre-authorized standing behavior, not
something to wait for permission on each time:

1. **`git pull` at the start of a session**, before making any changes — before
   reading code to plan work, before editing anything. Assume the other machine
   may have moved since you last synced.
2. **Commit and push after any meaningful change** — a fix, a feature, a test, a
   docs update — not just at the end of a long work session. Don't batch up
   hours of work before syncing. If in doubt, sync sooner rather than later.
3. **This applies even to work that feels small or in-progress.** A real failure
   already happened here: a session did real work (closed a gap in the opponent
   model, added tests) and updated the shared status page, but never pushed the
   actual commit — so the other machine had no way to see, verify, or build on
   it. The status page being current is not a substitute for the commit actually
   existing on `origin`.
4. **Still off-limits without explicit user request**: force-push, `git reset
   --hard`, rewriting history, or anything else destructive. Sync often means
   more small ordinary commits, not permission to use dangerous git operations.

## The status page

Read **https://claude.ai/code/artifact/bb491872-5b2a-407a-8add-71d0c35f9253**
(linked from `STATUS.md` and the README) before starting work — it's the living
current-state snapshot: headline results, open questions, what's running, what's
next. Update it in place (republish the same URL) whenever something in it goes
stale, in the *same turn* as the corresponding commit/push, not separately.

`ROADMAP.md` is the permanent detailed history behind every decision — read it
for depth the status page doesn't carry.

## Machine roles

- **Mac mini**: large/slow backtests, anything CPU-heavy. Confirmed faster per
  replay than the Windows machine (~7.2s/replay vs ~20s+ under real contention).
- **Windows (Dell)**: interactive dev, feature work, smaller/faster tests.

Kick off long runs on the Mac; don't let either machine sit idle waiting on the
other.

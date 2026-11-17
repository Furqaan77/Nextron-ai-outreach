# Claude instructions for this repository

This is the Nextron Outreach Engine project, built with the BMAD method. **Before doing anything, read `HANDOFF.md`.** It has the full context, every decision made, the current status and the exact procedure to continue.

## Rules
- **Planning is final.** Don't re-decide what's already in the brief, PRD, architecture spine, or `.memlog.md` decision logs unless Mac asks. Check them before asking Mac a question.
- **The architecture is binding.** Code in `nextron-engine/` must obey `ARCHITECTURE-SPINE.md` (AD-1..AD-22). Import rules are enforced by `uv run lint-imports`.
- **Build stories with `/bmad-build`,** tracking progress in `_bmad-output/implementation-artifacts/sprint-status.yaml`. On 2026-11-17 Mac approved building in **parallel tracks**, each in its own git worktree, merged into `main` story by story. `HANDOFF.md` §4 has the procedure. Without that setup, build one story at a time, in order.
- **Git:** one local commit per story. The remote `origin` is https://github.com/Furqaan77/Nextron-ai-outreach.git (branch `main`). Push only when the user explicitly asks.
- **Security and compliance:** never send cold email from nextronai.com; never automate LinkedIn or Instagram; every outbound email needs human approval.
- **Communication:** Mac prefers plain English, fast paths and few checkpoint pauses.

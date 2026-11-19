# Nextron Outreach Engine

An in-house lead-research and outreach engine for **Nextron AI**. It finds companies with a visible, recent reason to need custom AI or automation. It drafts short, evidence-based emails in the CEO's name and sends them only after a human approves each one. Interested replies go to the CEO's phone.

It runs locally on one Windows PC and costs under $100 a month to operate.

> **New here, or continuing on another account? Read [HANDOFF.md](HANDOFF.md).**

## Repository layout

| Folder | Contents |
|---|---|
| `nextron-engine/` | The application: Python 3.14, uv, FastAPI, SQLite. See [nextron-engine/README.md](nextron-engine/README.md). |
| `_bmad-output/planning-artifacts/` | Brief, PRD, architecture spine, epics and stories (all final) |
| `_bmad-output/implementation-artifacts/` | Sprint status, per-story specs, deferred work |
| `docs/research/` | Company profile, outreach laws and limits, target segment research |
| `docs/playbook/` | The Sales Playbook that the engine's AI follows |
| `docs/overview/engine-overview.html` | A plain-English visual overview. Open it in a browser. |
| `_bmad/`, `.claude/skills/` | The BMAD method (v6.12.4) used to plan and build this |

## Quick start (engine)

```powershell
cd nextron-engine
uv sync --frozen
$env:NEXTRON_ENV = "dev"
uv run nextron doctor          # checks the machine
uv run nextron serve           # dashboard on http://127.0.0.1:8766
uv run nextron open            # opens the dashboard in your browser (one-time code)
curl.exe -i http://127.0.0.1:8766/health   # 401 without a session: expected
uv run pytest                  # full test suite
uv run lint-imports            # architecture import rules
```

## Status

Planning is complete: 73 requirements, 22 architecture rules, 11 epics and 146 stories. The build is in progress. Stories 1.1–1.15 (scaffold, logging, persistence, dashboard entry, CSRF and safe rendering, typed settings, credentials, environment isolation, Google OAuth, durable jobs, triggers and heartbeat, ops alerts, metering and budget, `nextron doctor`, engine health panel, database backups, encrypted off-PC backups) the monthly restore test (1.18), restore and recovery mode (1.19), company facts (4.3), sending inboxes (4.7), campaigns (4.2), the logon-task spike (1.20) and supervisor (1.21, so all of Epic 1), the Suppression List and its page (3.3, 3.5), the contactability gate (3.4), live Eligibility (3.6), Provenance and basis display (3.7), erasure on request (3.13), the AI test gate and test sets (4.5, 4.6), export (3.15), domain authentication checks (6.5), erasure outside the database with receipts (3.14), the erasure ledger (3.16), the unsubscribe Worker code (3.10, not deployed), enrollment and Draft slots (4.8, 4.9), inbox warm-up caps (6.4), the LinkedIn/Instagram no-automation contract (10.1), the pipeline board (9.7), unsubscribe polling (3.11), the Places search adapter (8.2), Draft revisions (4.12), unsubscribe monitoring (3.12), Places and Signal Inbox Lead supply (8.3, 2.16), email validation (2.14), today's capacity (4.10), manual pipeline stages and call outcomes (9.1, 9.4), LinkedIn account setup and channel permissions (10.2, 10.7), unsubscribe tokens (3.8), the compliance footer (4.11), the Playbook snapshot (4.4), and all of Epic 2, Stories 3.1, 3.2 and 4.1 (Segments seed, one Lead per organization, CSV import, the Leads list and Lead page, the website crawler, the Claude gateway, prompt versioning, US/UK Market rules, each Contact's Market and timezone, the default Sequence) are done. The rest is being built in parallel tracks; see HANDOFF.md §4. Live status is in [`_bmad-output/implementation-artifacts/sprint-status.yaml`](_bmad-output/implementation-artifacts/sprint-status.yaml).

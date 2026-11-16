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

Planning is complete: 73 requirements, 22 architecture rules, 11 epics and 146 stories. The build is in progress. Stories 1.1–1.5 (scaffold, logging, persistence, dashboard entry, CSRF and safe rendering) are done, and Story 1.6 is next. Live status is in [`_bmad-output/implementation-artifacts/sprint-status.yaml`](_bmad-output/implementation-artifacts/sprint-status.yaml).

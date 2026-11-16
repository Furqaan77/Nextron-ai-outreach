# Handoff: Nextron Outreach Engine

**Read this first if you are a new Claude session or a new developer.** It records where the project stands, every decision made so far, and exactly how to continue. Last updated 2026-11-17.

---

## 1. What this is

**Nextron AI** (www.nextronai.com) is a 17-person custom software and AI agency based in San Jose. Furqaan Ahmed is the CEO and Bushra Ahmed the co-founder. It builds custom AI (private LLMs, chatbots, voice agents), CRM/ERP systems, automation and dashboards. **It currently has no live clients and has never done outbound sales.**

This repository holds the **Nextron Outreach Engine**: an in-house system that finds companies showing public signs of a problem Nextron solves, writes short evidence-based emails in Furqaan's name, has a human approve **every** email, sends them safely, and hands interested replies to Furqaan. **The only success metric is paying clients.**

The project owner and operator is **Mac** (the user). Mac prefers fast paths, few checkpoint pauses, and plain-English explanations.

## 2. Where everything is

| What | Path |
|---|---|
| Product brief (final) | `_bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/brief.md` (+ `addendum.md`: setup checklist, legal rules, budget, rejected options) |
| PRD (final, FR-1..FR-73) | `_bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/prd.md` (+ `addendum.md`, `changes-v2.md`, review files) |
| Architecture spine (final, AD-1..AD-22) | `_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md` (+ `reviews/`) |
| Epics and stories (final, 11 epics, 146 stories) | `_bmad-output/planning-artifacts/epics.md` (+ `epics-validation.md`) |
| Sprint tracking | `_bmad-output/implementation-artifacts/sprint-status.yaml` |
| Story specs and build artifacts | `_bmad-output/implementation-artifacts/` (`spec-1-1-…md`, `epic-1-context.md`, `deferred-work.md`) |
| **Decision logs** (the "why" behind everything) | `.memlog.md` in each planning folder (brief, prd, architecture) and `_bmad-output/party-mode/memories/installed/.memlog.md` |
| Research | `docs/research/` (company profile, outreach landscape and laws, ICP segments) |
| Sales Playbook (runtime AI rules, v0.2.0) | `docs/playbook/sales-playbook.md`; sources pinned in `docs/playbook/sources/agency-agents/` (MIT, commit 8ae6164) |
| Plain-English overview page | `docs/overview/engine-overview.html` (open in a browser) |
| **The code** | `nextron-engine/` (Python 3.14, uv project) |
| BMAD method (installed, v6.12.4) | `_bmad/` plus skills in `.claude/skills/bmad-*` |

**Before re-deciding anything, read the relevant `.memlog.md`.** Mac has already answered most questions.

## 3. Current status (2026-11-17)

| Phase | Status |
|---|---|
| Product brief | ✅ final |
| PRD | ✅ final (FR-28 amended 2026-11-15) |
| Architecture spine | ✅ final (amended 2026-11-15: any rejected Step stops the Sequence; owner-map additions) |
| Epics and stories | ✅ final, 146 stories |
| Sprint planning | ✅ done; readiness gate PASS |
| **Build** | **Stories 1.1–1.3 complete** (commits `c2e1b78`, `5adc1aa`, `b1a7384`; sprint status `review`). **Next: Story 1.4.** Built unattended overnight on 2026-11-17 per Mac's instruction; overnight decisions are listed under "Decisions made overnight (pending user review)" in each spec. |

**Story 1.1 result:** `nextron-engine/` scaffold with exact version pins, the spine's package layout (16 owner services), 28 import-linter contracts in 7 AD-1 groups, startup safety checks, a per-environment instance lock, `nextron serve`, `nextron doctor`, and `GET /health` on 127.0.0.1. 190 tests pass. The three-layer review found 37 findings: 15 fixed (including a high one, Uvicorn's exit code 3 colliding with "lock held"), 2 deferred, 20 rejected with reasons.

**Story 1.2 result:** redacted JSON-line logging (rotating `nextron.log`, redaction of secrets, tokens and emails on every path, including uncaught exceptions) and typed `DomainError`/`AdapterError`. 380 tests.

**Story 1.3 result:** one configured SQLite engine (WAL, `BEGIN IMMEDIATE`), thread-bound units of work (`@use_case`/`@command`), an append-only `events` table, the 10 AD-3 notification payloads, forward-only Alembic migrations run at `serve` start, and a PII registry for every column. 787 tests, 29 contracts.

**Story counts:** 146 total. Release 1 (Epics 1–7, first approved sends) is 109 stories, of which 3 are done. Release 2 (Epics 8–9) is 20. Release 3 (Epics 10–11) is 17.

## 4. How to continue building (exact procedure)

1. Open this folder in Claude Code. The BMAD skills in `.claude/skills/` load automatically.
2. Run `/bmad-build` and say: *"Implement Story 1.2 from `_bmad-output/planning-artifacts/epics.md`, tracked in sprint-status.yaml as `1-2-log-structured-redacted-json-lines-and-raise-typed-errors`. Mac prefers no checkpoint pauses."*
3. The workflow does the following:
   - reuses `epic-1-context.md` (it's cached; regenerate it if planning docs changed);
   - loads the previous done spec (`spec-1-1-…`) for continuity;
   - writes `spec-1-2-….md` and asks Mac to approve it;
   - implements via a subagent;
   - verifies against the git diff since the baseline commit;
   - runs three reviewers (blind hunter, edge-case hunter, verification gap);
   - triages every finding into the spec's Review Triage Log;
   - patches, re-verifies, marks the spec done and the story `review`;
   - commits locally.
4. Repeat story by story, in order. **Don't run stories in parallel**: they share files and one linear Alembic migration chain.
5. Optional: `/bmad-sprint-planning` with "show sprint status" at any time.

**Conventions established in Story 1.1 (keep them):**
- **Commits:** one local commit per story, in conventional style (`feat(engine): …`), ending with the Co-Authored-By trailer. The remote `origin` is https://github.com/Furqaan77/Nextron-ai-outreach.git (branch `main`, first pushed 2026-11-16). Push only when the user explicitly asks. The commits so far used a placeholder identity `Mac <mac@localhost>` because no git identity was set. Set your own with `git config user.name/user.email`.
- **Verification commands** (run from `nextron-engine/`):
  - `uv sync --frozen`
  - `uv run pytest` (expect all pass, `ai_eval` skipped)
  - `uv run lint-imports` (all contracts KEPT)
  - `NEXTRON_ENV=dev uv run nextron doctor` (all PASS)
  - `NEXTRON_ENV=dev uv run python -m nextron serve`, then `curl.exe http://127.0.0.1:8766/health`
- **Review diff:** exclude the generated `uv.lock` and the vendored `htmx-2.0.11.min.js`.
- **Ports:** 8765 prod, 8766 dev, 8767 test. **Data dirs:** `%LOCALAPPDATA%\Nextron\<env>\`.

**Model choice (discussed with Mac):**
- Opus 5.5 was used for all planning and for Story 1.1.
- Option under consideration: Sonnet 5.5 implements the routine stories while Opus reviews every story.
- Keep Opus for the roughly 25–30 safety-critical stories: 1.16–1.19, 3.3, 3.4, 3.8–3.11, 3.13–3.16, 4.12–4.14, 5.2, 5.4, 6.9, 6.10, 6.12, 7.1–7.3.
- Rough estimate, from one data point: 25–40 minutes per story on Opus, so about 45–70 hours for Release 1.
- Mac has not chosen yet. Ask.

## 5. Key decisions (all made by Mac unless noted)

**Scope and market**
- v1 Segments:
  1. Law firms with 5–50 lawyers (private AI).
  2. Mid-size distributors, logistics firms and light manufacturers (operations automation).
- Markets: **US + UK only**.
- UK cold email goes only to companies and LLPs. PECR treats sole traders and ordinary partnerships as individuals.
- Gulf, India, EU, Canada, CPA/RIA/clinics and real estate are later waves.
- Deal size: $10k–$30k first project. Offer ladder (Claude's choice, to validate on the first 10 calls): a reply-based ask → a $2,500–3,500 Private AI & Workflow Audit, credited toward the build → the build.

**Channels and approval**
- Cold email is the only automated channel.
- LinkedIn is human-sent: Tasks go to the account owner's Telegram (default Furqaan). LinkedIn bans automation and account sharing.
- WhatsApp and Instagram are for warm leads only, with opt-in.
- **Every email, follow-ups included, is individually approved.** Rejecting any Step stops that Sequence; "skip" doesn't.
- Hot replies: Furqaan replies from the Gmail app on his phone. Alerts reach him via Telegram, internal email and the dashboard, carrying a drafted reply, the booking link and two time slots.

**Platform and cost**
- Runs **locally on Mac's Windows 11 PC**, with a dashboard on localhost only and no remote access in v1. Mac confirmed the PC stays on during US and UK morning sending windows.
- The only off-PC piece is a Cloudflare Worker for RFC 8058 one-click unsubscribe.
- Moving to a cloud server was discussed on 2026-11-16 and is possible later as a bounded change. It is not chosen.
- Budget under $100/month: about $35–50 in month 1, about $70–85 from month 2 when Hunter Starter is added.
- Addresses published on a firm's own website count as trusted.
- Engine AI: `claude-haiku-5-5` reads (extraction, grounding check); `claude-opus-5-5` writes (drafts, Reply Drafts, reply classification, call prep). AI cap $20/month. Batch API for overnight work.

**Stack and engineering**
- Python 3.14 via uv, FastAPI + HTMX (vendored 2.0.11), SQLite (WAL), SQLAlchemy/Alembic, APScheduler 3.x, keyring.
- Exact pins are in the spine's Stack table and `nextron-engine/pyproject.toml`.
- `pytz` is allowed **only** as trafilatura's indirect dependency, and `nextron` code may never import it (Mac, 2026-11-15).
- **agency-agents:** the BMAD roundtable judged it safe and useful. Only its ideas were adapted into the Sales Playbook; the agents were **not** installed into `.claude/agents/`.

## 6. Open items

**Business setup (Mac and Furqaan; outside the software)**
- [ ] **Buy the sending domain and 3 Google Workspace inboxes, and start warm-up now.** The first send can happen 14 days after warm-up starts. Steps are in the brief's `addendum.md` §A.
- [ ] A Cal.com booking link on nextronai.com.
- [ ] A business postal address for the email footer.
- [ ] A Telegram bot and alert chat.
- [ ] A lawyer check of CAN-SPAM solicitation wording (PRD OQ-8).
- [ ] A UK privacy notice plus a legitimate-interests assessment.
- [ ] 1–2 case studies with client-confirmed numbers.
- [ ] Confirm the Workspace admin allows an Internal OAuth app. (Spike in Story 6.1.)
- [ ] Decide whose LinkedIn account is used (default Furqaan).

**Deferred engineering** (`_bmad-output/implementation-artifacts/deferred-work.md`)
- Enforcing "callers use only `app.<owner>.api`" needs an AD-1/AD-3 amendment.
- The clean-stop path (exit 0) on Windows belongs to Story 1.21.

**Architecture open questions:** see "Open Questions" in the spine (OQ-A*). They are slice-scoped spikes with owners.

## 7. Gotchas for a new machine or account

- `uv` installs Python 3.14 itself. The startup check requires SQLite 3.51.3 or later, which uv's 3.14.8 build has.
- `_bmad/render/` is gitignored, and BMAD regenerates it on first `/bmad-build`.
- The overview page was published as a private claude.ai artifact under the old account (`https://claude.ai/artifact/QGX5pvd9FqFyDnL7M4fkGb`). It may not be visible from the new account. The source is `docs/overview/engine-overview.html`; republish it if needed.
- Claude Code's auto-memory from the old account does not transfer. Its content is reproduced in section 5 above.
- Windows PowerShell: use `curl.exe`, not `curl`.

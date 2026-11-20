# START HERE: handoff for the next Claude

> **Status (2026-11-20 ~12:40 IST): the build is PAUSED so it can move to a new, stronger PC.**
> If you are on the new PC:
> 1. Make sure `setup_new_machine.ps1` has run (see `MOVE-TO-NEW-MACHINE.md`).
> 2. Read this file, then `CHECKPOINT.md`. It covers what is merged, what to verify, what to merge next and which stories to resume.
> 3. Then continue unattended. Mac's standing instruction is to keep building until Mac says stop.

Read this file first, then `CHECKPOINT.md`. This folder (`_handoff/`) holds everything a new Claude session or account needs to continue the build. It is deliberately **not tracked by git**: it is listed in `.git/info/exclude`, so it never ends up in a commit or a merge.

| File | What it is |
|---|---|
| `START-HERE.md` | This file: the concept, how the build works, and the rules |
| `CHECKPOINT.md` | Exact state when paused, and step-by-step resume instructions |
| `PROMPTS.md` | The prompt templates for track orchestrators and merge agents |
| `track-rules.md` | Rules every track orchestrator must obey (give its path in every prompt) |
| `merge-rules.md` | Rules every merge agent must obey |
| `suite_lock.py` | Runs the full test suite under a machine-wide lock |
| `memory/` | Copies of the previous Claude's memory notes about Mac and the project |
| `scratch/<track>/` | Per-track scratch space for helper scripts, diffs and logs |
| `MOVE-TO-NEW-MACHINE.md` | How to move the whole build to another PC, with `pack_for_move.ps1` (old PC) and `setup_new_machine.ps1` (new PC) |

---

## 1. What the project is

**Owner:** Mac, the user. Mac runs Nextron AI, a small AI, software and automation agency in San Jose; the CEO is Furqaan Ahmed.

**The product: the Nextron Outreach Engine**, a lead-generation and cold-outreach engine that runs locally on Mac's Windows PC.
- **Stack:** Python 3.14 (uv), FastAPI + HTMX dashboard, SQLite (WAL), SQLAlchemy, hand-written Alembic migrations.
- **Off-PC piece:** one Cloudflare Worker for one-click unsubscribe.
- **Code:** `nextron-engine/`.

**What it does, end to end:**
1. **Finds companies** (Leads) in two Segments, in the US and UK:
   - law firms with 5–50 lawyers (private AI);
   - mid-size distributors, logistics and manufacturers (ops automation).

   Sources are Google Places, Companies House, pasted Signals, CSV import and an optional job-board API.
2. **Researches each Lead:**
   - crawls the website and extracts facts and Evidence with Claude Haiku;
   - finds decision makers and picks a Primary Contact;
   - finds and verifies the email address;
   - scores the Lead into a Tier.
3. **Checks compliance before any contact:** CAN-SPAM, PECR and UK GDPR. That means Eligibility per Market, a suppression list, Provenance, erasure and export on request, and an erasure ledger re-applied after a backup restore.
4. **Drafts personalised emails with Claude Opus,** grounded in the Evidence. Every draft goes through deterministic grounding rules and an AI grounding check that fails closed.
5. **Puts every email in front of Mac for approval.** Follow-ups need approval too.
6. **Sends approved emails** from dedicated sending inboxes (Gmail), paced within warm-up caps and send windows, through a closed send gate.
7. **Reads replies,** classifies them, and handles opt-outs, bounces, hot leads, objections and referrals.
8. **Runs the pipeline:** stages, call bookings, call outcomes, call prep, and reports.
9. **LinkedIn, WhatsApp and Instagram are human-only.** The engine prepares LinkedIn Tasks and sends them to the account owner's Telegram; a person does the actual sending.

**Budget:** under $100 a month. The AI spend cap is $20 a month: claude-haiku-5-5 reads, claude-opus-5-5 writes.

---

## 2. Planning: final and binding

All of this lives under `_bmad-output/planning-artifacts/`. It was built with the BMAD method.

| Document | Contents |
|---|---|
| Product brief, then PRD | FR-1..FR-73 and the NFRs |
| `architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md` | **AD-1..AD-22, binding.** Import rules are enforced by `uv run lint-imports` (29 contracts). |
| `epics.md` | 11 epics and **146 stories**, each with acceptance criteria |
| `.memlog.md` files in the planning folders | Decision logs. Check them before re-deciding anything. |

**Rules from Mac, in `CLAUDE.md`:**
- Planning is final; don't re-decide it unless Mac asks.
- Build each story with the `/bmad-build` skill.
- One local commit per story.
- **Push only when Mac explicitly asks.** The remote is `https://github.com/Furqaan77/Nextron-ai-outreach.git`, branch `main`, and `origin/main` is still `67b8431`.

**Releases:**
- **Release 1** = Epics 1–7: first approved sends (109 stories).
- **Release 2** = Epics 8–9 (20 stories).
- **Release 3** = Epics 10–11 (17 stories).

Progress is tracked in `_bmad-output/implementation-artifacts/sprint-status.yaml`. Each story has a spec `spec-<key>.md` in that folder, with its Implementation Notes, Review Triage Log and "Decisions made overnight (pending user review)". `deferred-work.md` collects follow-ups.

`HANDOFF.md` in the repo root holds the long-form history, with per-story results and Mac's review list. It was last updated at commit `e4feb56` (after batch 5), so it doesn't yet cover batch 6 (4.17, 4.18, 5.2, 5.3, 6.6); see `CHECKPOINT.md`.

---

## 3. Hard rules (never break)

1. **Never send cold email from nextronai.com.**
2. **Never automate LinkedIn or Instagram.** Story 10.1's contracts enforce this.
3. **Every outbound email needs Mac's human approval.**
4. **No secrets** in files, the database, logs or job payloads. Credentials go in the OS keyring and are read at call time.
5. **Tests never reach real services:** Claude, Hunter, Companies House, Google, DNS, Cloudflare or a real browser. Every adapter has a fake in `PORT_FAKES` and is classified in `tests/live_guard.py`.
6. **Never change system state on Mac's PC:**
   - no Task Scheduler, registry or services;
   - no Credential Manager outside the test keyring;
   - no real `%LOCALAPPDATA%\Nextron`.

   A subagent once registered a real scheduled task; Story 1.21 now enforces this by contract.
7. **Never deploy the Cloudflare Worker.** No wrangler deploy, login or secret put.
8. **Never push.** Never rewrite git history: no filter-branch, rebase, amend or reset of existing commits.
9. **Mac's email address is private.** Don't send it to any external service.
10. **Commit identity:** `git -c user.name="Mac" -c user.email="mac@localhost" commit`. Every message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**How Mac likes to work:**
- Plain English, short answers, fast paths, few checkpoint pauses.
- Mac asked for **every agent and subagent to run as Opus 5.5 at medium effort**: Agent tool parameters `model: "opus"`, `effort: "medium"`.
- For a genuine intent gap in a story, choose the conservative, compliance-safe option and record it in the spec under "Decisions made overnight (pending user review)". Don't stop to ask.

---

## 4. How the build runs: parallel tracks

The build runs as several **tracks**, each in its own **git worktree** and branch. `main` is integration-only.

| Folder | Role |
|---|---|
| `e:\Nextron AI outreach` | branch `main`. **Integration only:** the main session and merge agents work here. |
| `e:\Nextron-epic1` | Track A |
| `e:\Nextron-epic2` | Track B |
| `e:\Nextron-track3` | Track C (idle, paused for machine load) |
| `e:\Nextron-track4` | Track D |
| `e:\Nextron-track5` | Track E |
| `e:\Nextron-chore` | Old chore worktree, unused |

### The cycle

**Building a story:**
1. The main session picks a story whose prerequisites are on `main` or on that track's own branch.
2. It creates a new branch in the track's worktree, from `main` or from the track's last story commit.
3. It runs `uv sync --frozen` in `nextron-engine`.
4. It launches a **track orchestrator agent** in the background, using the template in `PROMPTS.md`.
5. The orchestrator runs `/bmad-build`:
   - spec;
   - implementation subagent;
   - three reviewers: blind hunter, edge-case hunter and verification gap;
   - triage log written into the spec;
   - patch subagent;
   - present.
6. It runs the full suite once through `suite_lock.py`.
7. It commits once on its branch and reports back.

**Merging:**
1. When several stories are committed on tracks, the main session launches **one merge agent** with a batch of stories, by commit hash, in dependency order.
2. The merge agent merges each story with `--no-ff` and resolves conflicts by keeping both sides.
3. It **renumbers migrations** after main's head, keeping one linear Alembic chain: revision id = file stem `NNNN_<epic>_<story>_<slug>`. Then it updates every chain pin: HEAD constants, chain probes, `real_chain_plus`, `test_source_tree` FILES and the README migrations table.
4. It runs targeted tests after each merge and **one full suite at the end**.
5. After the merge is verified, the main session updates root `HANDOFF.md` and `README.md` in a `docs:` commit. Previous docs scripts follow a `docs_mergeN.py` pattern: string-replace edits with an assert that each anchor appears exactly once.

### The full-suite lock

`suite_lock.py` runs `uv run pytest -q -n 4 --dist loadgroup` and then `uv run lint-imports`, with `NEXTRON_ENV=test`. It used `-n 8` until the 16 GB PC ran out of RAM; raise it on a stronger PC (see `MOVE-TO-NEW-MACHINE.md` §5). Only one full suite may run at a time, because the tests share a fixed port and the PC is weak.

```
python "e:\Nextron AI outreach\_handoff\suite_lock.py" "<worktree>\nextron-engine" <label>
```

Run it as a **background** Bash call and wait for its completion notice.
- The lock is the folder `_handoff\pytest-suite.lock`.
- A lock is stale only when its holder process is dead, or after a 4-hour backstop.
- Waiters give up after 6 hours.
- Test temp files go under `_handoff\scratch\tmp\<label>`.

### Lessons learned (important)

1. **Machine load.**
   - Five tracks crashed Claude Code (0xC0000409) and exhausted Windows resources (WinError 1450). Keep **4 tracks at most**.
   - Outside the lock, targeted runs use `-n 2` at most, and never two pytest runs at once.
   - Under load a full suite takes about 25 minutes, and `tests/contracts` alone about 24 minutes at `-n 2`.
2. **Orchestrators sometimes hand back early.** It happens while a subagent they reached with SendMessage is still running, and that subagent's report then comes to the **main session**. Forward it to the orchestrator with SendMessage. The rule now is that patch work is always a NEW Agent call.
3. **Subagents must not commit or run the locked suite**; only the orchestrator does. Subagents must not create files outside their worktree; one left an empty `e:\_tmp_ignore`.
4. **Merge by commit hash, never by branch name,** because tracks keep extending their branches.
5. **`nextron/web/static/app.css`** is `-text` with mixed line endings (CRLF at the top). Edit it byte-preserving, with Python `open(..., newline="")`. Never use sed, which strips the CRs.
6. **Pins and lists drift between tracks.** A merge must combine every pinned list in registry order:
   - nav pins, settings lists, health sources, doctor checks;
   - job and trigger order, export sections and counts, erasure hook order;
   - `create_app`, `build_job_types` and `build_app` keyword arguments.
7. **Known intermittent failures under load:**
   - engine-startup timeouts in `test_serve_process`, `test_environment_isolation` and `test_run_supervisor`;
   - job-runner timing tests.

   If one fails alone, re-run the suite once and mention it in the report. Never weaken a test.
8. **Any new `contacts` column** must recreate the `trg_contacts_tombstone` erasure trigger. **Any new `enrollments` column** must update `trg_enrollments_bound`.
9. **Story 10.1's naming contract:** outside `nextron.domain`, never use "linkedin" or "instagram" in names; use "assist". New ports need a justified entry in `APPROVED_EXTRA_PORTS`.

---

## 5. Stories that need Mac (skipped so far)

These spikes need Mac's real accounts:
- **3.9:** confirm the Cloudflare DNS can host the unsubscribe Worker.
- **6.1:** Gmail send path and Internal OAuth in the real Workspace.
- **4.21:** measure AI cost per route against the $20 budget.
- **8.5:** choose a job-aggregator API. This one can mostly be done from public docs.

Real sending can't be switched on until 3.9, 6.1 and 6.2 are done with Mac.

---

## 6. Where to go next

Open `CHECKPOINT.md`. It lists exactly what is merged, what is committed on tracks, what was stopped mid-story, and the ordered resume steps.

---
name: outreach-engine-constraints
description: "Nextron AI outreach engine: the user's chosen scope, budget, channels, models, platform constraints, and BMAD progress (updated 2026-11-14)"
metadata:
  node_type: memory
  type: project
  originSessionId: 85872522-d5e0-4288-b624-7096a7dc9d2a
  modified: 2026-11-15T06:37:51.051Z
---

The user (Mac) runs Nextron AI, a custom AI/software/CRM/automation agency in San Jose (CEO Furqaan Ahmed; site www.nextronai.com). They are building an automated lead-gen and outreach engine in this repo with the BMAD method. Goal: paying clients; there are no live clients today.

Decisions (2026-11-14):
- v1 targets two Segments in the US + UK: law firms with 5–50 lawyers (private AI) and mid-size distributors/logistics/manufacturers (ops automation). UK cold email goes only to companies and LLPs.
- Channels: cold email is automated after approval. LinkedIn is human-sent: Tasks go to the account owner's Telegram. WhatsApp and Instagram are warm leads only.
- **Every email, follow-ups included, is individually approved.** Furqaan answers hot replies from the Gmail app on his phone.
- Budget under $100/mo (~$70–85 from month 2, with Hunter Starter). The AI cap is $20/mo: claude-haiku-5-5 reads, claude-opus-5-5 writes.
- Runs locally on Mac's Windows PC: Python 3.14 + FastAPI/HTMX + SQLite. The one off-PC piece is a Cloudflare Worker for one-click unsubscribe.
- agency-agents (MIT) sales personas were adapted into docs/playbook/sales-playbook.md, with pinned sources. They are NOT installed in .claude/agents.

BMAD progress: brief final → PRD final (FR-1..73) → architecture spine final (AD-1..AD-22) → epics.md final (11 epics, 146 stories; Slice 1 = Epics 1–7; validation in epics-validation.md), as of 2026-11-15. Sprint planning done 2026-11-15: the readiness gate passed after adding 4 owner-map entries to the spine, and _bmad-output/implementation-artifacts/sprint-status.yaml tracks all 146 stories (backlog). Build has started. The repo is git-initialized: local commits only, placeholder identity "Mac <mac@localhost>", no remote. Story 1.1 is done (commit c2e1b78): the code lives in nextron-engine/, with 190 tests and 28 import contracts. The epic-1-context.md cache is in implementation-artifacts. Next: Story 1.2, structured logging and typed errors. Deferred items are tracked in implementation-artifacts/deferred-work.md. Mac prefers fast paths and no checkpoint pauses. Files are under _bmad-output/planning-artifacts/. Overview artifact: https://claude.ai/artifact/QGX5pvd9FqFyDnL7M4fkGb

**Why:** the tight budget and the local machine rule out paid lead databases and 24/7 cloud sending. The legal stakes (CAN-SPAM, PECR, UK GDPR) drove strict send and contactability gates.
**How to apply:** follow the spine's ADs. Never send cold email from nextronai.com. Keep LinkedIn and Instagram human-paced. Check the memlogs in each planning folder before re-deciding anything.

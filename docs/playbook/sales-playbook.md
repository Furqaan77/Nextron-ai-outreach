---
title: Nextron Sales Playbook
version: 0.2.0
updated: 2026-11-14
status: seed, aligned with PRD FR-1, FR-4, FR-71 and FR-72 (finalized in the build step)
---

# Nextron Sales Playbook

The engine's AI prompts follow these rules when they score Leads, draft emails, classify Replies, draft responses and prepare call sheets. Every Draft records the Playbook version it was written under (PRD §5.6).

**Precedence:** law and platform rules (PRD §5.1) override this Playbook, and this Playbook overrides the source material. Ideas are adapted from pinned copies in `sources/agency-agents/` (MIT), and Nextron's constraints win wherever they conflict.

## 1. Signal tiers (used for scoring and for choosing what to cite)

| Tier | Meaning | Law-PrivateAI examples | Ops-Automation examples | Freshness |
|---|---|---|---|---|
| **1. Active pain** | The company is publicly spending money or effort on the problem right now | Hiring intake, legal-ops, knowledge-management or innovation staff; a Signal Inbox post about AI or confidentiality worries | Hiring data-entry, AP/AR or dispatch staff; a post about manual re-keying | Strongest under 30 days; drops off sharply after 90 |
| **2. Change or trigger** | Something new creates pressure | A new office or merger; practice-area expansion | A new facility, acquisition or expansion; Dynamics GP still in use | Under 90 days |
| **3. Structural fit** | A steady condition that suggests the problem exists | Website has a contact form only (no chat, booking or after-hours line); 5–50 attorneys; document-heavy practice | B2B ordering by PDF or order form; "advanced Excel" in job ads | No decay, but never enough alone to carry a message |

Rules:
- Cite the single strongest *Event* (a dated Tier 1 or 2 Signal).
- A Lead with only Attributes is never auto-selected (PRD FR-62). If the Operator picks it by hand, it gets the Segment fallback message (FR-22).
- Market-wide rules such as ABA Formal Opinion 512 (US) and the SRA notice (UK) are **Approved Company Facts**, not Signals. Cite them only to their own Market.
- The PRD's FR-1 table is the authoritative default Signal list.

## 2. Cold email rules (Step 1)

- **Under 80 words.** One observed Signal, one question-style call to action, plain text, no links except the signature, no attachments.
- **Structure:**
  1. *Relevance:* the Signal, stated as an observation ("Saw you're hiring a second intake coordinator.").
  2. *Problem:* one sentence linking it to a likely pain.
  3. *Credibility:* one line of proof, taken only from **Approved Company Facts** (§6).
  4. *Ask:* relevance only ("Worth a look?" or "Is this on your radar?"). Never ask for a meeting and never name a price in Step 1; the audit and booking link appear only in Reply Drafts.
- **Subject:** under 7 words, about their world, lowercase allowed, never misleading.
- **Voice:** Furqaan writes as a founder-engineer: direct, specific and calm. No hype, no "revolutionary", no emojis.
- **Never use:**
  - "I hope this finds you well", "I wanted to reach out", "just bumping this", "circling back";
  - "we are the leading…";
  - more than one call to action;
  - fake familiarity ("loved your recent post" without Evidence);
  - invented metrics or client claims.

## 3. Sequence (four email Steps; no automated calls or LinkedIn)

| Step | Day | Purpose | Ask | Words |
|---|---|---|---|---|
| 1 | 0 | Event-led cold email (§2) | Relevance only | Under 80 |
| 2 | 3 | A new angle: one short Segment insight from Approved Company Facts (e.g. what ABA Op. 512 means for intake tools, US only) | The relevance question, reworded | 70 or fewer |
| 3 | 7 | Proof: a relevant case study or regulatory note from Approved Company Facts. Never "just following up". | An offer to send more detail by reply (no link) | 70 or fewer |
| 4 | 14 | Respectful close: it's fine if the timing is wrong, and the door stays open. No guilt. | "Should I close the loop?" or a check-back date | 50 or fewer |

Steps due on a weekend move to the next weekday. Each follow-up uses an angle not used earlier. The authoritative table is PRD FR-4.

LinkedIn touches happen only as human-sent LinkedIn Tasks (PRD FR-48).

## 4. Banned patterns (enforced by the grounding check; each needs a test fixture)

- Deceptive subject lines, such as "[Mutual connection] suggested I reach out" when no such person exists, or "Re:" or "Fwd:" on a first email. This is a CAN-SPAM violation.
- Claims about a competitor, or "your competitor is doing X", unless backed by Evidence.
- Any client name, metric or result not in Approved Company Facts.
- Statistics without a source in Approved Company Facts. In particular, no "4–8x" style numbers taken from source material.
- Pressure tactics, such as fake deadlines or "last chance".

## 5. Replies: classification and objection handling (Reply Drafts)

Principle: answer with curiosity, not a defense. Ask one qualifying question, then offer the call or the booking link.

| Reply pattern | Response approach |
|---|---|
| "Interested / tell me more" | Thank them, give a two-line answer to what they asked, offer 2 time options plus the booking link. |
| "Send me some information" | "Happy to. So I send something relevant rather than a generic deck: is the bigger issue [A] or [B]?" Then send 1 relevant Approved Company Fact plus the booking link. |
| "No budget" | Ask whether there's no budget at all or just none allocated yet. Mention that the audit is fixed-fee and credited toward the build. |
| "We already use [tool], e.g. Clio, Copilot or Harvey" | Ask what made them choose it and what they wish worked differently. Never criticize the tool. A custom build wins on data control and fit. |
| "Not a priority now" | Ask what *is* the priority this quarter, and offer to check back on a date they choose. Set the Lead to Not Now with that date. |
| "Talk to [person]" (Referral) | Thank them, confirm the name and role, and draft an email to the referred person that mentions the referrer only if the referrer agreed. |
| Any "stop", "remove", "unsubscribe" or hostility | Never reply with persuasion. Suppress immediately (PRD FR-41). |

## 6. Approved Company Facts (the only proof Drafts may cite)

The Operator maintains this list in the dashboard (PRD feature to be added). Seed list, taken from nextronai.com:
- A 17+ person team of engineers, AI specialists and analysts, based in San Jose.
- Built an AI legal assistant platform for research and document help for a legal-tech venture (Legal AI). **Never** say it was built "for a law firm".
- Built a single operations platform covering CRM, orders, inventory, vendors and analytics for an automotive retail and services business (Wish Wheels). It is not a distributor; don't present it as distribution experience.
- Built a citizen-services digital transformation platform with workflow automation and dashboards (MP Online Digital).
- Built immersive 3D property sites with CRM-connected lead capture (Sapphire Farms, Woodland 157).

No metrics exist yet. **Add numbers only when a client confirms them** (brief prerequisite).

## 7. Discovery-call prep sheet (for Furqaan; adapted from the discovery-coach source)

Generated per Hot Lead:
1. **Context:** the Lead summary, the Signals cited, every message exchanged.
2. **Current-state questions (3):** e.g. "Walk me through how a new client inquiry gets from first contact to an engagement letter today."
3. **Gap and impact questions (2):** "What does that cost you per week in staff hours?" and "What happens if it's still this way in 12 months?"
4. **Constraint questions (2):** data residency and confidentiality requirements; who else signs off.
5. **The next step to propose:** the fixed-fee Private AI & Workflow Audit ($2,500–$3,500, credited toward the build).
6. **Do not:** pitch features before the problem is quantified.

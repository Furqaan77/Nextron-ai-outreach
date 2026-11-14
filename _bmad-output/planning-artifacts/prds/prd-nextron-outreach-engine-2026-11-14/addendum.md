---
title: "PRD Addendum: Nextron Outreach Engine"
created: 2026-11-14
updated: 2026-11-14
---

# PRD Addendum: Nextron Outreach Engine

Technical detail and options for the architecture step, which decides. These are recommendations and constraints, not decisions. Business setup, market rules and rejected product alternatives are in the brief's addendum (`../../briefs/brief-nextron-outreach-engine-2026-11-14/addendum.md`). Selling rules are in the Sales Playbook (`docs/playbook/sales-playbook.md`).

## 1. Platform constraints (fixed)
- One Windows 11 PC and a single user. Mac has confirmed the PC is on during the US and UK morning Send Windows. Full alert coverage for US afternoons needs it on until 17:00 US Pacific time (PRD §5.3).
- Python (through `uv`, already installed for BMAD) and Node 24 are available.
- Dashboard: a local web UI served on `localhost` only.
- Store: an embedded, file-based database (SQLite-class), with no database server.
- Background work: a long-running worker process that starts automatically at logon or boot and survives sleep and wake. A Windows Task Scheduler trigger or a service wrapper can do this.
- One component runs off the PC: the Unsubscribe Endpoint (PRD FR-70), on a free hosted platform. It holds only opaque tokens and request times.

## 2. External integrations
| Need | Recommended | Notes |
|---|---|---|
| Send and read email | Gmail API with OAuth for Google Workspace Sending Inboxes | Gives threading (`threadId`) and polling of inbound mail and the Sent folder, for exactly-once sending (FR-35), reply detection (FR-39) and Manual Reply detection (FR-65). Google relays all mail, which meets the PTR and TLS sender rules. The alternative, SMTP + IMAP with app passwords, is simpler but has weaker threading and status detection. |
| Business discovery | Google Places API (New) Text Search | The website URL is an Enterprise-tier field, so FR-5 searches bill as Enterprise (1,000 free requests a month). Text Search bills per request, and each request returns a page of several places. Confirm the page size and SKU. **Maps Platform terms restrict storing Places content:** plan to keep only `place_id` long-term (FR-5). |
| UK registry | Companies House REST API (free key; 600 requests per 5 minutes) | Legal form for the PECR check (FR-12). Accounts category and SIC codes as size clues (FR-6). |
| Job signals | Careers pages (FR-10) and ATS boards detected on the Lead's own site: Greenhouse, Lever and Ashby public job-board JSON endpoints | Queried one company at a time by board token, they enrich known Leads but can't discover new ones (FR-7). Add other ATS hosts only after checking each endpoint and its terms. An optional job-aggregator API may discover new Leads (FR-60, OQ-7). Keep request rates polite. |
| Email finding and verification | Website extraction → pattern inference → Hunter Domain Search (1 credit per domain) → Hunter verification (0.5 credit per address) → Prospeo free tier | Free tiers: Hunter 50 credits a month; Prospeo 100 a month, with API access listed as "limited". Add Hunter Starter (2,000 credits a month) in month 2. SMTP RCPT probing needs port 25, which home ISPs usually block, so don't build v1 around it. |
| One-click unsubscribe | Google Apps Script web app or Cloudflare Worker (free tiers) | Accepts the RFC 8058 POST, plus a GET for clients that open the URL. The engine polls it with a shared secret (FR-70, OQ-6). |
| Domain checks | DNS lookups (SPF, DKIM selector, DMARC) and DNS blocklist queries | For FR-64 and FR-38. The Google Workspace default DKIM selector is `google`. |
| AI | Claude API | Use a small, fast model for extraction, classification and the grounding pass, and a stronger model for Drafts. The architecture step sets model IDs, cost sizing and prompt caching, against FR-57's $20 default budget. |
| Alerts | Telegram Bot API (free) + Gmail API send to Furqaan's main inbox | Telegram needs a bot token and the target chat ID. The same channels carry the daily LinkedIn Task list (FR-48). |
| Booking | Cal.com link (static URL inserted into Reply Drafts) | No API integration in v1. After v1, Cal.com's availability API could supply the FR-43 time slots. |

**Credentials** (FR-56): the Claude API key; Google Places key; Hunter and Prospeo keys; Companies House key; Telegram bot token; a Gmail OAuth grant per Sending Inbox; the Unsubscribe Endpoint shared secret; an optional job-aggregator key.

## 3. Design notes behind specific FRs
- **FR-10 rendering:** to keep crawling cheap, use a headless browser only when the raw HTML holds little text.
- **FR-21 grounding check:** a second, cheap AI pass compares each claim in the Draft with the Evidence excerpts and Approved Company Facts. Rule-based checks cover word count, link count in Step 1, Step 1 subject prefixes ("Re:", "Fwd:"), banned phrases from Playbook §4, and hedged wording for Absence Evidence.
- **FR-73 AI Test Sets:** keep fixtures as files under version control, next to the prompts and the Playbook. Run them locally whenever a version changes. Seed the Reply set with synthetic examples, then add Operator overrides (FR-40) as real Replies arrive.
- **FR-64 authentication:** check relaxed DMARC alignment of the DKIM `d=` domain or the SPF domain with the From domain.
- **FR-34 scheduling:** Send Window calculations use the recipient's timezone, taken from the Contact's or Lead's location. The US has several timezones; the UK has one. The randomized gap applies per inbox.
- **FR-35 exactly-once sending:** write a send intent (`queued → sending`) before calling the API. On restart, reconcile any `sending` record against the inbox's Sent folder before any retry. Match on a unique header, for example a custom `X-` ID or the Message-ID.
- **FR-65 Manual Reply detection:** reuse the FR-35 Sent-folder query. Any message with the SENT label in a Campaign thread whose Message-ID isn't in the engine's send log is a Manual Reply.
- **FR-70 one-click unsubscribe:** use a random token per Step, with no email address in the URL. Headers: `List-Unsubscribe: <https://…/u/TOKEN>, <mailto:…?subject=unsubscribe-TOKEN>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. The DKIM signature must cover both headers. The endpoint answers the POST with success and no login or redirect. The engine maps each token to its Contact locally.
- **FR-53 retention:** run a scheduled cleanup job. Suppression entries store a SHA-256 hash of the normalized address.

## 4. Volume and capacity model (for sizing)
- **Capacity:** 3 Sending Inboxes × 30 a day = 90 Steps a weekday at full ramp, or about 1,950 a month (21.7 weekdays).
- **Steady state** with a 4-Step Sequence: about 22 new Contacts a weekday plus about 68 follow-ups. That is about 470 new Contacts a month, a little more as replies and bounces end Sequences early.
- **Address mix** (PRD FR-15 assumption): about 40% of Primary Contacts have a *Published* address: more for law firms, whose bio pages often list email, and fewer for Ops Leads. Published addresses skip verification and aren't capped. The other ~60% are *Guessed* and need a *Verified* result before sending. This replaces an earlier model that expected most addresses to be *Likely* while capping *Likely* sends at 30%, which held sending to about 40 a day.
- **Credits at full ramp:** about 280 Guessed Contacts a month. About 30% of verifications fail ("invalid", "accept-all" or "unknown"), so that is about 400 attempts × 0.5 credit. Domain Search adds up to 1 credit per new domain, for roughly 450–650 credits a month. Hunter Starter's 2,000 covers this about 3 times over, even if the Published share is far lower than assumed.
- **Month 1 on free credits:** about 150 credits (Hunter 50 + Prospeo 100) give about 100 Verified Contacts. With Published Contacts added, that is about 170 new Contacts in the month, or 8–12 a weekday. If month 1 shows a low Published share, buying Hunter Starter earlier is the lever.
- **Funnel at capacity:** about 470 Contacts a month give about 19 replies at a 4% reply rate, and about 7 positive replies at a 1.5% positive reply rate. If about half of those become calls, that is 3–4 calls. So SM-2's 4+ calls a month needs above-average conversion, which the Tier A focus (FR-62) aims at. The brief's 500–600 prospects a month is more than 4-Step Sequences at 90 sends a day allow.
- **Discovery:** about 2–3× the Leads needed: roughly 50–70 discovered and enriched a day, or about 1,000–1,500 a month. Re-checks (FR-61) come on top and need no Places calls. At several places per Text Search request (page size to confirm, §2), this fits the 1,000 free Enterprise requests a month. FR-57 stops Places discovery before any paid use.
- **AI workload:** about 2–2.5× the brief's sizing basis, because of more enrichment, a grounding pass per Draft, Reply Drafts and Call Prep Sheets. To stay inside the $20 default, use the §2 model split, run AI extraction on re-checks only when a page changed, and pre-score candidates cheaply before full enrichment.

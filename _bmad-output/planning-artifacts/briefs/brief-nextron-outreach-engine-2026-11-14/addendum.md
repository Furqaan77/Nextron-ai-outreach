---
title: "Addendum: Nextron Outreach Engine"
created: 2026-11-14
updated: 2026-11-14
inputs:
  - docs/research/outreach-landscape-2026.md
  - docs/research/icp-segments-2026.md
---

# Addendum: Nextron Outreach Engine

This addendum holds detail that supports the brief but is too detailed for it. **Setup:** A (do first). **PRD:** B (compliance), F, G. **Architecture:** C, D (prompt design), E, F.

## A. Sending infrastructure setup checklist (do first)

1. **Buy one lookalike domain**, e.g. `nextronai.co`, `getnextronai.com` or `nextron-ai.com` (about $10–15 a year). Never send cold email from `nextronai.com`.
2. **Redirect** the lookalike domain to `www.nextronai.com`.
3. **Create 2–3 Google Workspace Business Starter inboxes** on it (about $7 per user a month), with real-looking names, e.g. `furqaan@`, `f.ahmed@`, `team@`. Add a profile photo and a signature to each.
4. **Set up DNS:** an SPF record that includes Google; DKIM (2048-bit, enabled in the Workspace admin); and DMARC `v=DMARC1; p=none; rua=mailto:…` to start.
5. **Leave open tracking off.** It hurts deliverability. If it is ever turned on, add a custom tracking domain first.
6. **Warm up for 2–4 weeks:** start at 5–10 sends a day per inbox, and increase gradually to 25–30 a day. The warm-up method (friendly contacts, a paid tool, or engine-run) is an open decision; see F.
7. **Cap cold sends at 30 per inbox per day** for the first month. With 3 inboxes that is about 90 a day, or about 1,800 a month on weekdays.

## B. Channel and market rules

| Channel | Role in v1 | Hard rule |
|---|---|---|
| Cold email | Primary; automated after approval | Lookalike domain only; one-click unsubscribe; postal address in every email; opt-outs suppressed immediately (CAN-SPAM's limit is 10 business days) |
| LinkedIn | Assisted: the engine drafts, a human sends | No bots or scraping (User Agreement §8.2); at most 100 invites a week |
| Instagram | Warm leads only | A business may message only after the user engages first (24-hour window) |
| WhatsApp | Warm leads only | Only numbers the prospect shared and opted in; approved templates outside the 24-hour window |
| Phone | Warm leads only: the engine supplies a call sheet; a human calls | No automated calling |

| Market | Cold email law | In v1? |
|---|---|---|
| US | Allowed (CAN-SPAM) | Yes |
| UK | Allowed for corporate subscribers (companies, LLPs). Individual subscribers (sole traders and ordinary partnerships in England, Wales and Northern Ireland) need consent (PECR) | Yes, corporate subscribers only; check each firm's legal form on Companies House |
| EU (excluding Germany) | Varies by country; needs a legitimate-interest assessment | No (later wave) |
| Germany | Consent required even for B2B | No (cold email blocked) |
| Canada | Only addresses the recipient published conspicuously (CASL) | No (later wave) |
| UAE / KSA | Consent required (PDPL) | No (cold email blocked; later wave via LinkedIn and referral) |
| India | No email-specific law; DPDP consent duties from about May 2027 | No (later wave) |

## C. Lead sources and signals

| Data | Source | Cost and limit |
|---|---|---|
| Business discovery (name, address, website, phone, rating) | Google Places API (New) Text Search | Free: 5,000 Pro and 1,000 Enterprise requests a month |
| Firm size, people, emails, tech and pain signals | Crawl of the company's own website (team, about, careers and contact pages) | Free |
| UK company data, officers and legal form | Companies House API | Free; 600 requests per 5 minutes |
| UK law firm check | SRA register (⚠ API availability unconfirmed) | Free |
| Hiring signals | Greenhouse, Lever and Ashby public job boards; company careers pages | Free |
| Email finding and verification | Pattern guess + MX check; Hunter and Prospeo free tiers. (Reacher, a self-hosted SMTP verifier, needs port 25 and is likely unusable on a home connection.) | Free: Hunter 50 credits a month; Prospeo 100 a month |
| LinkedIn pain posts | **Found by a human** and pasted into the engine's signal inbox | Free |
| Reviews (e.g. "never called back") | Google Places review fields (Enterprise SKU) | Within the free limit above |

| Signal | Segment 1 (law) | Segment 2 (ops) |
|---|---|---|
| Hiring | "Intake", "legal ops", "knowledge management" or "innovation" roles | Data entry, AP/AR clerks, dispatchers, inventory coordinators; "advanced Excel" roles |
| Systems | Clio, iManage or NetDocuments in job ads | Mentions of Dynamics GP or legacy systems |
| Website | Contact form only (no chat, booking or after-hours line) | B2B order form or PDF catalog instead of a portal |
| Reviews | Unreturned calls | — |
| Profile and news | Document-heavy practices (immigration, PI, family, real estate); 5–50 attorneys on the team page | New facility, expansion or acquisition |

## D. Message guidelines

- **Evidence only:** personalization may cite only facts stored in the lead's evidence record. If evidence is missing, fall back to a segment-level message flagged for closer review.
- **First email:** under 80 words, one observed signal, one question-style call to action, no attachments, and no links except the signature.
- **Four emails over about 14 days.** Each follow-up adds new value (a short insight, a relevant case study, a regulatory note), never "just bumping this."
- **Sender:** Furqaan Ahmed, CEO, in plain text. The signature carries the postal address and an opt-out line.
- **Testing:** two variants per segment each month, so the funnel view can compare them.

## E. Monthly budget

| Item | Cost a month |
|---|---|
| Google Workspace, 3 inboxes | about $21 |
| Lookalike domain | about $1 (about $12 a year) |
| Claude API (enrichment and drafting, about 600 leads) | about $10–25 [ASSUMPTION; size in the architecture step] |
| Google Places, Hunter, Prospeo, Companies House | $0 (free tiers) |
| Cal.com booking link | $0 |
| **Total** | **about $35–50** |

**Headroom:** about $50–65 a month under the $100 cap. Options that would use it:
- a paid warm-up tool, $15–40 a month (see F);
- a sending VPS, about $5 a month (see F);
- Hunter Starter, $34 a month billed annually, if finding email addresses becomes the bottleneck.

Taking all three at the top price would exceed the cap.

## F. Open decisions

- **Warm-up method:** friendly contacts by hand, a paid warm-up tool, or engine-run warm-up between the Nextron inboxes.
- **Machine and timezone:** where the PC is, and whether it can stay on during US and UK morning hours. The alternative is a $5-a-month VPS for sending only.
- **Sending path:** Gmail API (OAuth) or SMTP with app passwords. Reply detection uses the Gmail API either way.
- **Tech stack:** to be chosen in the architecture step. Constraints: Windows, local, single user, a local web dashboard, a SQLite-class store, and the Claude API for drafting.

## G. Rejected alternatives and why

- **Bulk LinkedIn or Instagram scraping or automation:** it breaks the platforms' terms, gets accounts restricted, and puts Furqaan's personal LinkedIn (a sales asset) at risk.
- **Buying an Instantly + Apollo + Clay stack:** $300–1,000+ a month is over budget, and Nextron would own neither the data nor the process.
- **Targeting every industry in every country:** generic messages get below-median reply rates, and consent laws block cold email in several of the requested markets.
- **Real estate as a v1 segment:** US home sales are at a 30-year low and the space is crowded with SaaS. Real estate is kept for the Gulf and India waves, where Nextron's 3D case studies are.
- **Fully autonomous sending:** Mac chose approve-then-send, because one invented "fact" sent to hundreds of firms would damage the brand.

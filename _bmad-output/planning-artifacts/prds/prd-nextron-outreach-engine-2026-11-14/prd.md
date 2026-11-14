---
title: "PRD: Nextron Outreach Engine"
status: final
created: 2026-11-14
updated: 2026-11-14
inputs:
  - _bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/brief.md
  - _bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/addendum.md
  - docs/research/nextron-company-profile.md
  - docs/research/outreach-landscape-2026.md
  - docs/research/icp-segments-2026.md
  - docs/playbook/sales-playbook.md
---

# PRD: Nextron Outreach Engine

## 0. Document purpose

This PRD defines what the Nextron Outreach Engine must do in v1, for Mac (builder and Operator), Furqaan Ahmed (CEO and Sender), and the downstream BMAD steps. It builds on the product brief, its addendum, and the Sales Playbook. §3 defines terms, §10 indexes every `[ASSUMPTION: …]` tag, and `addendum.md` holds technical options.

## 1. Vision

Nextron AI has no live clients and no outbound sales process. The Outreach Engine is a single-user desktop system on the Operator's Windows PC. Every day it:
1. finds a few companies with public evidence of a problem Nextron solves;
2. writes short, evidence-grounded emails in Furqaan's name, following the Sales Playbook;
3. queues every message for human approval;
4. sends approved messages safely from warmed Sending Inboxes;
5. turns replies into booked discovery calls; Furqaan can answer hot replies from his phone.

It's deliberately narrow and careful: two Segments (private AI for law firms; operations automation for mid-size distributors, logistics firms, and light manufacturers) in two Markets (US and UK). It enforces sending limits and legal rules to protect Nextron's domains and reputation. On channels whose rules forbid automation (LinkedIn, Instagram, WhatsApp), it only prepares messages for a human to send.

Success means paying clients: the first paid engagement within 90 days of the first cold email, then at least one a month. Every scope decision is judged by whether it shortens the path to the first signed engagement (§6.3).

## 2. Target user

### 2.1 Jobs to be done
- **Operator (Mac):** "Each morning, give me a short queue of well-researched prospects and ready-to-send messages that I can check and approve in under 30 minutes, without worrying that we're breaking a rule or burning our domain."
- **Sender (Furqaan):** "Tell me at once when a real prospect is interested, with context and a reply I can send from my phone, so I can get them onto a call while they're warm."
- **Business (Nextron):** "Fill the pipeline with $10k–$30k engagements for under $100 a month, with a process we own and can expand to new Segments and Markets."

### 2.2 Non-users (v1)
Prospects only receive email, and may use the booking link or one-click unsubscribe. There's one Operator role with no sign-in, and v1 isn't sold to others (§7).

### 2.3 Key user journeys

- **UJ-1. Mac clears the morning queue.** Before the first UK Send Window, Mac reviews 24 first emails and 61 follow-ups due today, researched overnight. Each card shows the Contact, Lead Tier, draft, and the Evidence and Approved Company Facts behind each claim. Using `A` (approve), `E` (edit), and `R` (reject with a reason), he fixes a wrong practice area, rejects a sole practitioner, and batch-approves 40 routine follow-ups, finishing in 25 minutes. **Edge case:** a claim with no matching Evidence or Approved Company Fact flags the card red and blocks approval until Mac edits it out.
- **UJ-2. Furqaan answers a hot reply from his phone.** At 2:10 PM ET, an Ohio managing partner replies: "Interesting. We've been worried about putting client files into ChatGPT. What would this look like?" It's classified *Interested*. By 2:15 PM, Furqaan, away from the PC, has a Telegram message and an email with the reply, a two-line Reply Draft offering two times tomorrow, and the booking link. He edits the draft in the Gmail app and sends it at 2:31 PM. The next poll logs the Manual Reply, withdraws the Reply Draft, and moves the Lead to *Interested*. The partner books; Mac moves the Lead to *Call Booked*; Furqaan gets the Call Prep Sheet by email. Response time: 21 minutes (SM-8).
- **UJ-3. Mac feeds in a LinkedIn signal.** A Texas distributor's COO posts: "We're hiring two more people just to re-key orders into our ERP." Mac pastes the post URL, text, and author into the Signal Inbox. Within 15 minutes, the engine has enriched the company, rated it Tier A, queued a first email citing the post, and added a LinkedIn Task for the account owner.
- **UJ-4. A prospect opts out** by clicking unsubscribe in their mail client or replying "remove me." Within one polling cycle, every pending Step stops and the address joins the Suppression List permanently (FR-41, FR-70).
- **UJ-5. The PC was off** for part of the UK Send Window. On wake, the engine sends only what still fits each Send Window and Daily Cap, with no duplicates or burst (FR-35, FR-36).

## 3. Glossary

- **Operator:** the dashboard user on the local PC: Mac, or Furqaan when at the PC. There's one Operator role.
- **Sender:** Furqaan Ahmed, CEO, whom every outbound email comes from. Every Sending Inbox is in his name.
- **Segment:** a target buyer definition (FR-1). v1 has *Law-PrivateAI* and *Ops-Automation*.
- **Market:** a country with its own outreach rules (FR-2). v1 has *US* and *UK*; other countries are blocked.
- **Lead:** one target organization, in one Segment and one Market.
- **Contact:** a person at a Lead (name, role, email, optional LinkedIn URL). A Lead has zero or more; one is the Primary Contact (FR-13).
- **Signal:** a public, observable fact suggesting a Lead has a problem Nextron solves: an Event or an Attribute, with a Signal Tier.
  - **Event:** a dated "why now" fact, such as a job posting, post, or acquisition. Events feed Signal Score and lose weight with age (FR-11).
  - **Attribute:** a lasting "why you" fact, such as practice area, attorney count, or Dynamics GP in use. Attributes feed Fit Score and don't decay. A *score-only* Attribute can change scores but is never cited in a Draft.
- **Signal Tier:** a Signal's strength (Sales Playbook §1): *1 Active pain*, *2 Change or trigger*, or *3 Structural fit*.
- **Evidence:** stored proof behind a Signal or fact, with source URL(s) and retrieval time. Its type matches the observation: *Quote* for something stated (a verbatim excerpt of up to 300 characters); *Absence* for something not found (what was sought, and every page checked); *Count* for a number (the number, what was counted, and the pages checked).
- **Provenance Record:** where and when each item of personal or company data, including each email address, was obtained, and the rule that made the Contact Eligible (FR-51).
- **Fit Score, Signal Score, Lead Score:** Fit Score (0–100) measures how well a Lead's Attributes and size match its Segment; Signal Score (0–100), the strength and recency of its Events. Lead Score combines them and ranks Leads within a Lead Tier (FR-17).
- **Lead Tier:** *A*, *B*, or *C*, set by the Lead's live Events (FR-62).
- **Address Class:** an email address's trust label: *Published*, *Guessed*, *Verified*, or *Invalid* (FR-15).
- **Eligibility:** whether a Contact may legally and safely receive cold email (FR-18).
- **Campaign:** a Segment × Market pairing with an active Offer, a Sequence, and one or more Variants (FR-3).
- **Variant:** a version of a Campaign's messaging, for comparison (FR-3).
- **Sequence:** the ordered Steps a Contact receives in a Campaign (FR-4).
- **Step:** one scheduled email in a Sequence. Step 1 is the first; Steps 2–4 are follow-ups.
- **Draft:** an AI-written message awaiting approval: an email Step, a Reply Draft, or a LinkedIn, WhatsApp, or Instagram message.
- **Approval Queue:** the due Drafts awaiting the Operator's decision (FR-26).
- **Sales Playbook:** the versioned selling rules in `docs/playbook/sales-playbook.md` (v0.1.0 at this PRD's date) that the AI prompts follow (FR-71).
- **Approved Company Facts:** the Operator-maintained list of the only claims a Draft may make about Nextron, its case studies, its Offer, regulations, and statistics (FR-72). Each entry is an Approved Company Fact.
- **AI Test Sets:** the grounding set (labeled Drafts) and Reply set (labeled Replies) that gate AI changes (FR-73).
- **Sending Domain:** a lookalike domain used only for cold email (FR-32).
- **Sending Inbox:** a mailbox on a Sending Domain, in the Sender's name, that sends and receives Campaign email. Each is also in the Gmail app on the Sender's phone.
- **Daily Cap:** the most Steps a Sending Inbox may send per day (FR-33, FR-34). Reply Drafts and Manual Replies don't count.
- **Warm-up Ramp:** the schedule that raises a Sending Inbox's Daily Cap to its target (FR-33).
- **Send Window:** the recipient's local business hours for sending Steps (default 08:00–17:00, weekdays).
- **Reply:** an inbound message on a Campaign thread, human or automatic.
- **Reply Class:** *Interested, Question, Objection, Referral, Not Now, Not Interested, Unsubscribe, Data Question, Out of Office,* or *Other* (FR-40). An *Objection* carries a type from Sales Playbook §5. A *Data Question* asks how Nextron got the person's details. A bare "no" (the footer's opt-out wording) is *Unsubscribe*.
- **Bounce:** a delivery-failure notice: *hard* (bad address), *temporary*, or *policy* (rejected for authentication or reputation); see FR-44. A Bounce isn't a Reply.
- **Reply Draft:** an AI-drafted response to a Reply.
- **Manual Reply:** a message the Sender writes and sends himself in a Campaign thread from a Sending Inbox, such as from Gmail on his phone. It counts as approved (FR-65).
- **Hot Lead:** a Lead whose latest Reply Class is *Interested*, *Question*, or *Referral*, or an *Objection* the Operator marks as engaged.
- **Pipeline Stage:** a Lead's commercial status: *New → Queued → Contacted → Replied → Interested → Call Booked → Call Held → Audit Proposed → Audit Won → Build Proposed → Won*, or at any point *Not Now* (with a re-contact date), *Lost*, or *Disqualified* (FR-45). *Interested* means the Lead is a Hot Lead, whichever hot Reply Class made it one.
- **Call Prep Sheet:** the engine's one-page brief for the Sender before a discovery call (FR-68).
- **Call Outcome:** the Operator's record of how a scheduled discovery call went (FR-69).
- **Suppression List:** addresses and domains never to be contacted again (FR-50).
- **Unsubscribe Endpoint:** the free hosted web endpoint receiving one-click unsubscribe requests (FR-70).
- **Signal Inbox:** where the Operator pastes signals found by hand (LinkedIn posts, job posts, news) for processing (FR-8).
- **LinkedIn Task:** a daily to-do that the LinkedIn account owner carries out by hand (FR-48).
- **Channel Permission:** the record that a prospect started contact on Instagram or WhatsApp, or explicitly gave their number for WhatsApp contact. Drafts on those channels require one (FR-24).
- **Offer:** what a Campaign asks for and sells. The v1 ladder: a reply, then a fixed-fee $2,500–$3,500 Private AI & Workflow Audit credited in full toward a build, then a $10k–$30k build [ASSUMPTION: the prices are validated on the first 10 held calls (FR-69); they sit deliberately below the research benchmarks to make the first yes easy].
- **Release Slice:** one of three build increments (§6.3). Slice 1 is the smallest set that can send the first approved email.

## 4. Features

### 4.1 Campaign and Segment setup
**Description:** v1 ships two Segments, the US and UK Markets, one Campaign per Segment × Market, and default Sequences from the Sales Playbook: as configuration in Slice 1, with editing screens in Slice 3 (§6.3). Realizes UJ-1.

#### FR-1: Segment definitions
The Operator can create and edit Segments: industry keywords, size range, decision-maker roles, disqualifiers, weighted Signal types, and discovery queries.
- Shipped: *Law-PrivateAI* (5–50 lawyers; managing partner or owner) and *Ops-Automation* (distributors, logistics firms, and light manufacturers with estimated revenue of $10M–$200M; COO or VP Operations first, owner or CEO as fallback, and Controller, CFO, or Finance Director for finance-role Events).
- Default Signals (below, from Sales Playbook §1) are editable; default weights rank Tier 1 above Tier 2 above Tier 3.
- Default disqualifiers, applied before scoring: Law, fewer than 5 lawyers, or an office of a firm with more than 50; Ops, fewer than 20 staff, or part of a group with more than 1,000. A match marks the Lead Disqualified and shows the rule.
- Segment changes apply to Leads discovered afterward, and to Operator-triggered re-scoring.

| Segment | Signal | Class | Signal Tier | Evidence | Source |
|---|---|---|---|---|---|
| Law | Hiring intake, legal-ops, knowledge-management, or innovation staff | Event | 1 | Quote | Careers page, ATS board, Signal Inbox |
| Law | Post about AI or client confidentiality | Event | 1 | Quote | Signal Inbox |
| Law | New office, merger, or practice-area expansion | Event | 2 | Quote | News or press pages, Signal Inbox |
| Law | 5–50 attorneys; document-heavy practice | Attribute | 3 | Count; Quote | Team and practice pages |
| Law | Contact form only (no chat, booking, or after-hours line) | Attribute, score-only | 3 | Absence | Website |
| Ops | Hiring data-entry, order-entry, AP/AR, or dispatch staff | Event | 1 | Quote | Careers page, ATS board, Signal Inbox |
| Ops | Post about manual re-keying or ERP pain | Event | 1 | Quote | Signal Inbox |
| Ops | New facility, acquisition, or expansion | Event | 2 | Quote | News or press pages, Signal Inbox |
| Ops | Dynamics GP still in use | Attribute | 2 | Quote | Job ads, website |
| Ops | Ordering by PDF or order form with no portal; "advanced Excel" in job ads | Attribute | 3 | Quote or Absence | Website, job ads |

Market-wide facts (ABA Formal Opinion 512, the SRA notice) are Approved Company Facts (FR-72), not Signals.

#### FR-2: Market rules
Each Market sets whether cold email is allowed, eligible legal forms, Send Window timezones, required footer content, and Draft spelling and vocabulary (US or UK English).
- US: cold email allowed.
- UK: corporate subscribers only (companies and LLPs); free-webmail addresses are never eligible.
- The Market is set per Contact: the stated office location, and the country of any country-code email domain, must be in an enabled Market (so .ca or .de is blocked). If the location is unknown, the Lead's country applies, and the Provenance Record says so.
- Contacts outside enabled Markets get no cold email.

#### FR-3: Campaigns and Variants
The Operator can create a Campaign (Segment × Market), attach an Offer, set a Sequence, and run up to two Variants, splitting new Contacts evenly between active Variants.
- Pausing a Campaign stops its future Steps from entering the Approval Queue; the Operator can resume it.
- A UK Campaign can't be activated until a legitimate-interests assessment reference and date are recorded on it.

#### FR-4: Sequence configuration
The Operator sets the number of Steps (default 4), delays (default days 0, 3, 7, and 14; weekend Steps move to the next weekday), and each Step's brief: purpose, word limit, allowed content, and ask. Default briefs (Sales Playbook §3):

| Step | Day | Content | Ask | Words |
|---|---|---|---|---|
| 1 | 0 | One observed Signal; one sentence on the likely problem; one credibility line from Approved Company Facts | Relevance only, such as "Worth a look?" No meeting request or price | Under 80 |
| 2 | 3 | New angle: one short Segment insight from Approved Company Facts (Law-PrivateAI default: what ABA Op. 512 or the SRA notice means for their intake or document work) | The relevance question, reworded | 70 or fewer |
| 3 | 7 | Proof: one relevant case study or regulatory note from Approved Company Facts; never "just following up" | Offer to send more detail by reply (no link or attachment) | 70 or fewer |
| 4 | 14 | Respectful close: the timing may be wrong; the door stays open. No guilt or urgency | "Should I close the loop?" or a check-back date of their choosing | 50 or fewer |

- Step 1 has exactly one call to action and no links except the signature. Its subject has fewer than 7 words, is about the prospect's world, and never misleads.
- Each follow-up uses an angle or fact not used earlier in the Sequence.
- No Step names a price or proposes the audit; only Reply Drafts do (FR-43).

### 4.2 Lead discovery
**Description:** Candidate Leads for each active Campaign come from CSV imports, the Signal Inbox, and public APIs, never scraping (FR-49), at 2–3× the number needed, since scoring and Eligibility discard many. Hiring Signals enrich known Leads (FR-7). Realizes UJ-1 and UJ-3.

#### FR-5: Places discovery
The engine can search Google Places by Segment query and location (such as "law firm" in Columbus, OH) and record each candidate's place ID, name, and website URL.
- It keeps long-term only what the Maps Platform terms allow [ASSUMPTION: the place ID only; the name and website are held until the Lead's own website replaces them; verify in architecture]. Durable company facts come from the Lead's website (FR-10).
- FR-57 meters the Pro and Enterprise quotas separately and stops Places discovery before any paid use.

#### FR-6: Registry discovery (UK)
The engine can search Companies House by keyword and SIC code, and retrieve any UK Lead's registered name, number, legal form, status, officers, accounts category, and SIC codes (the last two are Fit Score size clues).
- Dissolved or inactive companies are discarded.

#### FR-8: Signal Inbox intake
The Operator can paste one or more items into the Signal Inbox: a URL with its text and an optional author or company, such as LinkedIn or job posts from a short manual search. The engine identifies the organization, creates or updates the Lead, and attaches the content as Evidence with source = "manual". Realizes UJ-3.
- Items are processed on submission, not overnight; a resulting Step 1 Draft reaches the Approval Queue within 15 minutes.
- An item whose organization can't be identified stays in the Signal Inbox, flagged for the Operator.

#### FR-9: Deduplication
The engine merges candidates that point to the same organization (same website domain, Companies House number, or place ID) into one Lead. It never creates a second Lead for an organization already in the system, including Disqualified and suppressed ones.

#### FR-59: CSV import
The Operator can import Leads from CSV: organization name, website, and country, plus optional Segment and Contact name, role, email, and LinkedIn URL. The form requires the list's origin (such as "state bar directory, compiled by hand"), recorded in each row's Provenance Record.
- Rows are deduplicated (FR-9), Market-checked (FR-2), and enriched (FR-10 to FR-13) like any Lead.
- CSV email addresses are *Guessed* unless also on the Lead's website, so imports can't bypass verification (FR-15).
- Until automatic selection ships (FR-19, Slice 2), the Operator picks imported and Signal Inbox Leads for drafting by hand, within FR-19's daily capacity.

#### FR-60: Job-aggregator discovery (optional)
The engine can query one job-aggregator API by Segment job titles and location to find organizations not yet in the system, such as Ohio law firms hiring intake staff. Each result becomes a candidate Lead, with the posting as Event Evidence.
- Off by default; enabled only after the architecture step confirms the API's terms allow this use (OQ-7) [ASSUMPTION: a free or low-cost aggregator API exists; its quota is unverified].
- FR-57 meters and stops its calls like any quota.

### 4.3 Enrichment and Signal detection
**Description:** Each Lead gets an evidence-backed profile from its own website and public sources. The AI may extract and summarize, but every stored fact must point to Evidence.

#### FR-10: Website research
The engine crawls a Lead's homepage plus team, about, careers, contact, services, and news, press, or blog pages, up to a configured limit (default 15).
- It obeys robots.txt and waits at least 1 second between requests to a domain.
- It renders near-empty pages (raw HTML with almost no text) in a headless browser before extracting, and detects chat and booking widgets from scripts as well as visible text.
- It extracts what the company does, locations, size clues (attorneys listed, staff count, locations, fleet), named people with roles, published email addresses, technology mentions, and contact methods (form, chat, booking link, phone).
- It keeps only people in the Segment's decision-maker roles; others are discarded after Primary Contact selection.

#### FR-11: Signal detection
The engine detects the Signals in the Segment's list (FR-1), storing each as an Event or Attribute with its Signal Tier and supporting Evidence of the matching type (§3). Signals without Evidence are never stored.
- Each Event has an event date (posted, published, or announced; else first seen), from which age is measured. Defaults (Sales Playbook §1): Tier 1 Events keep full weight for 30 days and reach zero at 90; Tier 2 Events keep full weight for 90 days, then expire. Job-posting Events stay live only while the posting is open.
- Attributes don't decay; they're re-checked on re-crawl (FR-61).
- Drafts state an Absence only in hedged wording ("I couldn't find online booking on your site") and never cite score-only Attributes.

#### FR-12: Legal form check (UK)
For UK Leads, the engine retrieves the legal form from Companies House (such as Ltd, LLP, or partnership not registered). It's confirmed only when Evidence links the Lead to the record: the company number on the Lead's site, or a matching registered name and address.
- A UK Lead whose legal form isn't a corporate subscriber, or can't be confirmed, is ineligible for cold email but stays available for LinkedIn Tasks.

#### FR-13: Decision-maker selection
The engine picks each Lead's Primary Contact: the best match to the Segment's decision-maker roles, preferring the role tied to the strongest Event (such as the Controller or Finance Director for AP/AR hiring). With no named decision-maker, the Lead isn't drafted and shows "no decision-maker found".
- The Operator can change the Primary Contact.

#### FR-7: Hiring-signal enrichment
For each known Lead, the engine collects job postings from (a) careers pages FR-10 finds and (b) applicant-tracking-system (ATS) boards the site links to or embeds, from a configured list of hosts with public job pages (default: Greenhouse, Lever, and Ashby; the architecture step confirms any other host's endpoint and terms). It matches titles and descriptions against the Segment's hiring Signals.
- Each posting stores its URL, title, posted date if shown, first-seen date, and live or closed status at each check.
- Hiring data yields new Leads only through the Signal Inbox (FR-8) and the optional aggregator (FR-60), never by scraping job sites (FR-49).
- [ASSUMPTION: about 20–30% of enriched Leads have a live Tier 1–2 Event found automatically; check the real share by source in the first week of enrichment.]

#### FR-61: Periodic re-check
The engine re-enriches Leads on a schedule to find new Events:
- Eligible Leads not in an active Sequence, and not Disqualified, Lost, or suppressed: fully every 30 days (default), with careers pages and ATS boards every 7 days. AI extraction runs only on changed pages.
- *Not Now* Leads: on the prospect's chosen re-contact date (FR-66).
- A new Event re-scores the Lead and can change its Lead Tier.

### 4.4 Contact finding and verification
**Description:** Each Primary Contact gets a sendable address, found as cheaply as possible, with its origin recorded.

#### FR-14: Email discovery
The engine tries these sources in order, giving each address a Provenance Record:
1. the Contact's address published on the Lead's website;
2. a pattern inferred from other published addresses at the domain;
3. Hunter Domain Search, once per domain, to learn the pattern;
4. a finder API (Hunter, then Prospeo [ASSUMPTION: Prospeo's free tier allows API use]) within configured monthly credit limits.

Hunter Starter (2,000 credits a month) is added in month 2, at full ramp (§5.2, §6.4).

#### FR-15: Email validation and Address Class
Before a Contact can become Eligible, the engine checks the address (syntax, MX record, not disposable, not on the Suppression List) and assigns an Address Class:
- *Published:* on the Lead's own website (Quote Evidence). Trusted; not capped.
- *Guessed:* pattern-derived, from a finder API, or from a CSV import. Never sent until verified.
- *Verified:* a Guessed address a verification API returned as valid; "accept-all" or "unknown" leaves it Guessed.
- *Invalid:* failed a check or hard-bounced. Never sent.
- Verification credits are spent only on Guessed addresses.
- [ASSUMPTION: about 40% of Primary Contacts have a Published address, more for law firms than Ops Leads.]

#### FR-16: Bounce learning
A hard Bounce marks the address Invalid. After 2 pattern-derived addresses at one domain hard-bounce, the engine stops pattern inference for that domain.

### 4.5 Scoring and Eligibility
**Description:** Only Leads with a current reason to write, and legally eligible Contacts, reach drafting automatically.

#### FR-17: Lead Score
The engine calculates Fit Score, Signal Score, and Lead Score for every enriched Lead, showing contributing factors and their Evidence on the Lead page.
- [ASSUMPTION: Lead Score = 0.5 × Fit + 0.5 × Signal; the weights are configurable.]

#### FR-62: Lead Tier
The engine assigns each enriched Lead a Lead Tier: *A*, at least one live Tier 1 Event; *B*, a live Tier 2 Event and no Tier 1; *C*, no live Tier 1–2 Event. It's shown on the Lead page and review card, and recomputed whenever an Event is added or expires.
- Tier C Leads are never selected automatically, whatever their Lead Score. The Operator may pick one by hand; its Drafts follow FR-22.

#### FR-18: Eligibility gate
A Contact is Eligible for cold email only if:
- its Market allows cold email (FR-2);
- its legal form is eligible (UK, FR-12);
- its address is Published or Verified;
- neither address nor domain is on the Suppression List;
- it never asked not to be contacted, and FR-63 allows contact;
- the Lead isn't Disqualified or Lost.

Ineligible Contacts never enter the Approval Queue as email Drafts; the reason is shown.

#### FR-19: Daily selection
Each day, the engine picks Eligible Leads for new Sequences: Tier A first, ordered by newest Event (ties broken by Lead Score), then Tier B, ordered by Lead Score. The number is the Sending Inboxes' capacity left after due follow-ups, capped at the day's total capacity divided by the number of Steps so later follow-ups always fit [ASSUMPTION: about 22 new Contacts a weekday at full ramp (3 inboxes × 30 a day) from month 2; about 8–12 in month 1, when free verification credits are the limit].
- Leads must also reach a configured minimum Lead Score (default 50), which alone never admits a Tier C Lead.
- Unused capacity shows on the dashboard as "signal shortfall".
- At most one Contact per Lead is in an active Sequence at a time.

#### FR-63: Re-contact rules
Re-contact follows these rules:
- A Sequence that ended without a Reply may restart only 90 or more days after its last Step, and only on an Event first seen after it ended; the new Step 1 cites that Event.
- A *Not Now* Lead gets one re-contact Draft on the prospect's chosen date (default 90 days after the Reply), after Eligibility and Suppression List checks are re-run.
- After *Not Interested* or *Unsubscribe*, the Contact is never contacted again on any channel; the Lead moves to *Lost* and leaves re-checks and automatic selection.

### 4.6 Message drafting
**Description:** The AI drafts in Furqaan's voice (Sales Playbook §2), using only Evidence about the Lead and Approved Company Facts about everything else. Brand risk lives here, so grounding is a hard rule.

#### FR-20: Grounded email Drafts
For each selected Contact, the engine drafts every Step for the Campaign's Variant, following the Step briefs (FR-4). Each Draft has a subject, body, and Step number, and records the Evidence, Approved Company Facts, Sales Playbook version, and prompt version it used.
- Every factual claim about the Lead maps to stored Evidence; every claim about Nextron, its work, a regulation, or a statistic matches an Approved Company Fact.
- Step 1 cites exactly one Signal: the strongest live one.

#### FR-21: Grounding check
After generation, the engine checks each Draft, subject included, and flags:
- Lead claims without supporting Evidence, and Absence claims not in hedged wording;
- claims about Nextron, its case studies, regulations, or statistics that don't match an Approved Company Fact allowed for the Draft's Segment and Market;
- every banned pattern in Sales Playbook §4, plus fake familiarity, false urgency, attachments, links in Step 1, more than one call to action, and follow-ups that say "just following up" or repeat an earlier Step's main point.

Failed Drafts are flagged red and can't be approved until they're edited and pass a recheck. FR-73 sets the check's accuracy bar.

#### FR-22: Fallback copy
For a hand-picked Tier C Lead, the engine uses the Segment-level template instead of inventing specifics, and flags the Draft "generic" for closer review.

#### FR-23: Signature and footer
Every email Draft ends with Furqaan's signature, Nextron's legal name, a business postal address, and an opt-out line (such as "Not relevant? Reply 'no' and I won't contact you again."). UK emails add a one-line privacy notice: where the details came from, linking to Nextron's privacy notice. The footer counts as signature for FR-4's link rule.
- Editing can't remove the footer.
- No email is sent while the legal name, postal address, or (UK) privacy-notice link is blank.

#### FR-24: Channel Drafts
For each LinkedIn Task (FR-48), the engine drafts an optional connection note (200 characters or fewer; no pitch or link) and a first message (300 characters or fewer) that adds something the emails didn't.
- WhatsApp or Instagram Drafts need a Channel Permission; an email reply, or a number in a signature or on a website, doesn't qualify. The Channel Permission records what the prospect did, when, and its Evidence.
- Instagram Drafts are created only within 24 hours of the prospect's last message there; the card shows when that window closes.

#### FR-25: Regenerate
The Operator can regenerate any Draft with an optional instruction, such as "shorter" or "lead with the SRA notice". The grounding check reruns.

### 4.7 Approval Queue
**Description:** Every email, follow-ups included, needs explicit Operator approval when due, unless the Sender writes it himself (FR-65). **This changes the brief**, which approved a Sequence once and then sent follow-ups automatically; Mac chose per-email approval. FR-29 to FR-31 handle the resulting load. Realizes UJ-1.

#### FR-26: Queue contents
The Approval Queue shows every Draft due within 24 hours, in order: Reply Drafts (Hot Leads first); Tier A Step 1 Drafts, newest Event first; other Step 1 Drafts; follow-ups whose delay has passed. Within the Tier A group, newest Event first, with ties broken by Lead Score; within every other group, items sort by Lead Score.
- A follow-up enters only if the Contact hasn't replied, unsubscribed, or bounced.
- A follow-up whose Evidence or Approved Company Facts changed since drafting is regenerated before it enters.

#### FR-27: Review card
Each queue item shows:
- the Lead name, Segment, Market, Lead Tier, and Lead Score;
- the Contact's name, role, and Address Class;
- the Draft and its Sales Playbook version;
- for follow-ups, the earlier Steps sent;
- the Evidence and Approved Company Facts relied on, with clickable source links;
- the grounding check result.

#### FR-28: Decisions
The Operator can approve, edit then approve, regenerate, reject with a reason (wrong person, bad fit, wrong fact, not eligible, other), or skip.
- Rejecting a Draft for any Step stops that Contact's Sequence. Skipping doesn't. (Amended 2026-11-15; see architecture AD-4.)
- "Bad fit" can also disqualify the Lead.
- Skipped items stay in the queue for the next session.

#### FR-29: Keyboard-first review
Every FR-28 decision has a single-key shortcut, and the next card renders in under 300 ms after a decision. At full volume (about 90 items), median daily approval time is 30 minutes or less (SM-6).

#### FR-30: Batch approval of follow-ups
In v1, the Operator can approve several follow-ups that passed the grounding check together from a compact list. It shows each Draft's Contact, Step, subject, and full body, with Evidence and Approved Company Facts one key away, so every Draft is still seen before approval.

#### FR-31: Approval expiry and stalls
The engine handles delays as follows:
- An approved email unsent after 3 days (such as when the PC was off) returns to the Approval Queue for re-approval instead of sending stale.
- Each Step's delay counts from the previous Step's actual send, so late approvals shift the Sequence instead of bunching Steps.
- A follow-up unapproved 7 days after falling due is flagged "stalled" on the dashboard.
- The Operator can set away dates; no new Sequences are selected for them, so the backlog on return holds only follow-ups. Hot replies don't wait for the Operator (FR-65).
- Before sending a Step 1 that cites a job posting last checked more than 24 hours earlier, the engine re-checks it. If closed, the Draft returns to the queue flagged "Event expired".

### 4.8 Sending and sequencing
**Description:** Approved emails go out through the Sending Inboxes' own accounts, relayed by the mail provider, within Daily Caps and Send Windows, spaced like human sending: never twice and never in a burst. Realizes UJ-4 and UJ-5.

#### FR-32: Sending Inbox management
The Operator can connect, pause, and remove Sending Inboxes, each with a Sending Domain, Daily Cap, Warm-up Ramp, warm-up record, and health status.
- Inboxes on `nextronai.com` or its subdomains are refused.
- Each inbox uses the Sender's name as From name and a personal address (such as furqaan@); role addresses (team@, info@, sales@) are refused.
- The engine warns when a Sending Domain has more than 3 Sending Inboxes.

#### FR-64: Sending Domain authentication check
On connect and daily, the engine checks each Sending Inbox's domain: SPF includes the mail provider, a DKIM key is published, a DMARC record exists, and SPF or DKIM aligns with the From domain.
- Any failure blocks cold sends from that inbox and shows on the health panel (FR-58) until a recheck passes.

#### FR-33: Warm-up Ramp
Each Sending Inbox has a warm-up record: start date, method (manual or a tool), and current warm level (daily warm-up volume reached).
- Cold sends are blocked until the inbox has at least 14 days of recorded warm-up.
- The cold Daily Cap then starts at the recorded warm level (or the target, if lower), so warm inboxes aren't reset, and rises about 5 a week to a target of 30 a day. The ramp is editable.
- Sends by a warm-up tool still running on the inbox count toward a total daily ceiling (default 50), entered by the Operator or read from the Sent folder. The engine doesn't run warm-up exchanges itself (§6.2).

#### FR-34: Send scheduling
Approved Steps are sent only inside the recipient's Send Window, spread with randomized gaps (default 4–12 minutes per inbox).
- No inbox exceeds its Daily Cap.
- A Contact's Steps all come from one Sending Inbox, in one thread.
- Approved Reply Drafts are sent at once, in-thread; they don't count against the Daily Cap or wait for the gap or Send Window.

#### FR-35: Exactly-once sending
Each approved email is sent at most once, even if the engine crashes, the PC sleeps, or the network drops mid-send. If a send's outcome is unknown, the engine checks the Sent folder before retrying.

#### FR-36: Catch-up after downtime
After downtime, the engine uses only the current Send Windows' remaining capacity; missed sends roll to the next valid window. Realizes UJ-5.

#### FR-37: Stop rules
A Contact's Sequence stops immediately, canceling pending Steps, on:
- any Reply except *Out of Office*, from the Contact or anyone at the Lead's domain (a new person who replies becomes a Contact);
- a Manual Reply in the thread;
- a hard Bounce;
- an unsubscribe by any route (FR-41, FR-70);
- a manual stop;
- the Lead being marked Disqualified or Lost.

*Out of Office* delays the next Step until any stated return date; opt-out wording inside it counts as *Unsubscribe*. A Reply of uncertain class pauses the Sequence until the Operator decides (FR-40).

#### FR-38: Deliverability guardrails
The engine monitors each Sending Inbox and domain, and acts automatically:
- 7-day hard-bounce rate above 2% (with at least 50 sends): alert the Operator; above 3%: pause the inbox;
- policy Bounce: pause the inbox and alert at once;
- Sending Domain on a DNS blocklist (checked daily): pause its inboxes;
- Google Postmaster Tools spam rate at or above 0.1%: alert; at or above 0.3%: pause the domain's inboxes. "No data" is recorded as no data, not 0%;
- a Reply accusing Nextron of spam: count a complaint for SM-5 and alert the Operator.

### 4.9 Reply handling and alerts
**Description:** The engine reads every Sending Inbox, classifies Replies, alerts Furqaan on his phone, drafts responses, and logs replies he sends himself. Nothing reaches a prospect unless the Operator approved it or the Sender wrote it. Realizes UJ-2 and UJ-4.

#### FR-39: Reply detection
The engine checks each Sending Inbox's inbound mail and Sent folder at least every 2 minutes while running, linking each message to its Contact, Lead, and thread. Non-Replies, such as newsletters, are ignored.

#### FR-40: Reply classification
The engine assigns each Reply a Reply Class (Sales Playbook §5) and extracts any return date (*Out of Office*), the referred person and whether the referrer may be named (*Referral*), any re-contact date (*Not Now*), and the objection type (*Objection*).
- A rule-based opt-out check ("no", "remove", "unsubscribe", "stop", and variants) runs alongside the classifier. A match counts as *Unsubscribe* even if the classifier disagrees; the Operator can reverse it with a logged reason.
- If the classifier is uncertain, the Sequence pauses and the Reply is flagged for review; if any likely class is hot, the FR-42 alert still goes out.
- The Operator can override any class. Overrides join the Reply set, which sets the classifier's accuracy bar (FR-73).

#### FR-41: Unsubscribe and opt-out handling
An *Unsubscribe* Reply, a one-click or mailto unsubscribe (FR-70), or any Reply clearly asking not to be contacted adds the address to the Suppression List, stops the Sequence, and blocks all future channel Drafts for the Contact.
- Firm-level wording ("remove us") adds the whole domain.
- A *Not Interested* Reply also adds the address to the Suppression List.
- Opt-outs take effect within one polling cycle.

#### FR-42: Hot Lead alerts
When a Reply makes a Lead a Hot Lead, the running engine alerts the Sender within 5 minutes of its arrival by Telegram bot message, by email to Furqaan's main nextronai.com inbox, and by a highlighted dashboard notification.
- The Telegram message and email each contain the Lead and Contact, Reply text, Signal cited, Reply Draft (FR-43), booking link, two proposed time slots, and the Sending Inbox to answer from.
- If the Reply Draft isn't ready within 2 minutes, the alert goes out marked "draft to follow", and the draft follows in a second message.

#### FR-43: Reply Drafts for Hot Leads
For each Hot Lead Reply, the engine drafts a Reply Draft of 90 words or fewer (Sales Playbook §5). It answers the prospect's question in the first two sentences from Evidence and Approved Company Facts only, offers two specific 20-minute slots in the prospect's local time within the next 3 business days, gives the booking link as an alternative, and makes one ask.
- Slots come from the Sender's configured weekly availability [ASSUMPTION: no calendar integration in v1; the Sender checks his calendar before sending].
- Price appears only if the prospect asked. The audit is presented as a fixed-fee, low-risk first step credited toward a build.
- The Reply Draft tops the Approval Queue (FR-26). Once approved, it's sent in-thread from the same Sending Inbox, unless a Manual Reply went first (FR-65).

#### FR-65: Manual Reply detection
The engine logs any message in a Campaign thread's Sent folder that it didn't send (per its own send records) as a Manual Reply, with text and time. It counts as approval.
- Any pending Reply Draft for the thread is marked superseded and leaves the Approval Queue. Before sending an approved Reply Draft, the engine re-checks the thread for a Manual Reply.
- The Sequence stays stopped (or stops). The Lead moves to *Interested* if at an earlier Pipeline Stage, and SM-8's clock stops at the Manual Reply's send time.
- Manual Replies aren't grounding-checked and don't count against the Daily Cap. A Manual Reply to an address on the Suppression List alerts the Operator.

#### FR-66: Reply Drafts for objections, Not Now, and data questions
The engine drafts replies (Sales Playbook §5) for:
- *Objection:* acknowledge it in one line, answer briefly from Approved Company Facts, ask one question, and offer one next step. Never argue or criticize a named product.
- *Not Now:* thank them and confirm their check-back date, or ask for one. The Lead moves to *Not Now* with that date (FR-63).
- *Data Question:* state where the details came from (Provenance Record) and offer the opt-out; no pitch or booking link. A Reply that also asks for erasure or a copy of their data is flagged as a data subject request (FR-52).

These don't alert the Sender unless the Operator marks an Objection as engaged (Hot Lead). Their drafts join the queue's Reply Draft group.

#### FR-67: Referral hand-off
For a *Referral* Reply, including an auto-reply naming a successor, the engine adds the referred person as a new Contact at the same Lead, noting the referrer in the Provenance Record. The new Contact passes FR-14 to FR-18 like any other.
- The engine drafts a short thank-you to the referrer, and a Step 1 to the new Contact in a new thread from the same Sending Inbox, which then follows the Campaign's Sequence. It names the referrer only if they agreed (FR-40).
- Both Drafts go to the top of the Approval Queue.

#### FR-44: Bounce handling
The engine detects Bounces, attributes each to its Contact, and classifies it:
- *hard* (such as 5.1.x): the address becomes Invalid; the Bounce feeds FR-16 and the bounce rate;
- *temporary* (4.x.x): retried inside the Send Window; treated as hard after 3 failures;
- *policy* (5.7.x): the address is unchanged; the inbox pauses (FR-38).

### 4.10 Pipeline and warm follow-up
**Description:** A lightweight pipeline tracks Leads from first contact to won or lost, prepares and records discovery calls to test the Offer, and supports follow-up on channels the engine doesn't automate.

#### FR-45: Pipeline Stages
The engine moves Leads through *New*, *Queued*, *Contacted*, and *Replied* automatically; to *Interested* when a Lead becomes a Hot Lead; to *Not Now* on a *Not Now* Reply; and to *Call Held* (or back to *Call Booked*) from a Call Outcome (FR-69). The Operator moves Leads through the other stages by hand and can record a deal value and notes.

#### FR-46: Pipeline view
The Operator can view Leads by Pipeline Stage, filter by Campaign, Segment, Market, or date, and open any Lead's full history: Evidence, Signals, Contacts, every message sent and received, and stage changes.

#### FR-47: Warm-channel follow-up
For a Hot Lead, the Operator can request a drafted LinkedIn message, a WhatsApp or Instagram message (with a Channel Permission only, FR-24), or a call sheet (phone number, talking points from the Signals, and the Offer). The Operator sends or calls by hand, then logs the touch.

#### FR-68: Call Prep Sheet
For each Hot Lead, the engine prepares a Call Prep Sheet (Sales Playbook §7): context (Lead summary, Signals cited, every message exchanged); three current-state, two gap-and-impact, and two constraint questions suited to the Lead's Segment and Event; likely objections with their Sales Playbook §5 approach; Approved Company Facts to mention; the next step to propose (the audit, its price, and its credit toward a build); and a reminder not to pitch features before the problem is quantified.
- It uses only Evidence and Approved Company Facts and passes the grounding check.
- It shows on the Lead page and is emailed to the Sender's main inbox when the Lead enters *Call Booked*.

#### FR-69: Call Outcome logging
After a booked call's scheduled time, the dashboard prompts the Operator for its Call Outcome: held or no-show; the outcome (qualified or not and why; next step and date); whether the audit was proposed, and at what price; objections raised (Sales Playbook §5 types); and price reaction (accepted, pushed back, or not discussed).
- The Pipeline Stage updates from the record.
- On a no-show, the engine drafts a rebooking email with two new slots.
- After 10 held calls, FR-54 reports audits proposed and accepted, price reactions, and top objections, to validate the Offer (§8).

### 4.11 LinkedIn assist
**Description:** LinkedIn stays human-operated by the account owner; the engine only prepares the work and keeps volume safe. Realizes UJ-3.

#### FR-48: Daily LinkedIn Tasks
Each weekday, the engine prepares LinkedIn Tasks (default 10, about 10 minutes on a phone): Signal Inbox Leads first, then Hot Leads, then Tier A Leads 1–2 business days after their Step 1 is sent.
- Only the account's owner (default: Furqaan; OQ-5) carries out its Tasks, from that account; no account is shared. Drafts are in the owner's voice.
- Each Task gives the profile link (or, with no known URL, a search hint: name, company, and role), an optional connection note, and a first message for after acceptance. Notes are optional because free accounts limit them [ASSUMPTION: free accounts allow only a few notes a month, of up to 200 characters; verify for the account used].
- The engine never schedules more than 100 invitations per account in a rolling week.
- Tasks reach the owner's phone by Telegram or email. The owner or Operator marks each one sent, skipped, or accepted, and can log a found profile URL.

#### FR-49: No LinkedIn automation
The engine never signs in to LinkedIn, automates browser actions on LinkedIn or Instagram, or fetches LinkedIn pages. It never scrapes the Google Maps website or job sites such as Indeed or LinkedIn Jobs. LinkedIn URLs come only from the Signal Inbox, the Lead's website, or a human.

### 4.12 Compliance and data governance
**Description:** These rules keep Nextron legal under CAN-SPAM, UK PECR, and UK GDPR and protect prospects' data. They can't be turned off from the dashboard.

#### FR-50: Suppression List
The Suppression List holds addresses and, optionally, whole domains. It's checked at discovery, Eligibility, and drafting, before any channel Draft, and immediately before each send.
- The Operator can add entries by hand and import a list.
- Entries never expire.

#### FR-51: Provenance
Each Contact and email address has a Provenance Record, shown on the Lead page: source (URL, API name, CSV origin, referrer, or manual), retrieval date, Address Class, and the Market rule that made it Eligible, including the legitimate-interest basis for UK Contacts.

#### FR-52: Data subject requests
The Operator can erase a Contact on request, deleting their personal data and keeping only a hashed address on the Suppression List so they're never contacted again. The Operator can also export everything held on a Contact.

#### FR-53: Retention
Personal data on Contacts is deleted automatically after a configured period [ASSUMPTION: 6 months after discovery for Contacts never emailed; 12 months after the last Step for Contacts with no engagement]. Suppression entries are kept.

#### FR-70: One-click unsubscribe
Every Step carries a `List-Unsubscribe` header with an HTTPS URL on the Unsubscribe Endpoint and a mailto address, plus `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058).
- The Unsubscribe Endpoint is a free hosted service (Google Apps Script or a Cloudflare Worker; OQ-6). It records each request as an opaque token and time, returns success with no further step, and stores no email address or name.
- The engine polls it every 5 minutes while running, turning each request into an unsubscribe (FR-41); requests made while the PC is off are processed on the next run.
- It's the only engine component off the PC. Reply-based opt-out (FR-23, FR-41) remains.
- The health panel alerts the Operator if polling fails for more than 24 hours.

### 4.13 Funnel analytics
**Description:** Shows whether the engine produces clients, and which Segments, Markets, Lead Tiers, Signals, and Variants work best.

#### FR-54: Funnel dashboard
The Operator can see Leads discovered, Eligible, and Contacted; emails sent; bounce, reply, positive reply, and opt-out rates; Hot Leads; calls booked and held; no-shows; audits and builds won; and deal value, by Campaign, Segment, Market, Variant, Lead Tier, Signal type and source, Contact role, Sending Inbox, Step of first Reply, and time period.
- Rates are per contacted Contact, not per email.
- It also shows the share of Step 1 Drafts flagged generic, signal shortfall, hiring Events per 100 Leads crawled by source, Event-to-send time (SM-11), Hot Lead response time (SM-8), and the Offer validation report (FR-69).
- Variant comparisons show "not enough data" until each Variant has at least 150 contacted Contacts.

#### FR-55: Approval metrics
The engine records the Operator's daily approval time, rejection reasons, and the share of each Draft's characters changed by editing; more than 20% is a major edit for SM-7.

### 4.14 Settings and operations
**Description:** Configuration, credentials, quotas, and health live in one place.

#### FR-56: Credentials
The Operator can enter and update every external credential (listed in addendum §2) and the Sending Inbox connections, stored as §5.4 requires.

#### FR-57: Quotas and cost meter
The engine tracks usage per operation against each quota (such as Hunter: 1 credit per find, 0.5 per verification; Places Pro and Enterprise counted separately) and the month's AI spend against a monthly budget (default $20, alert at 80%).
- Reaching a quota or the AI budget stops non-essential work (discovery, enrichment, re-checks) and alerts the Operator. Paid Places usage never starts automatically.
- Quotas and the AI budget never block sending, reply handling, or unsubscribe polling.

#### FR-58: Health panel
The dashboard shows:
- whether the background worker is running;
- each job's last successful run (discovery, enrichment, re-check, sending, reply check, Unsubscribe Endpoint poll, backup);
- each Sending Inbox's status, days of recorded warm-up, authentication result (FR-64), and Daily Cap use;
- the last monthly restore test result;
- errors needing attention.

### 4.15 Sales Playbook and Approved Company Facts
**Description:** The AI works from selling rules and approved claims kept outside the code for review and versioning. The Sales Playbook says how to write; Approved Company Facts say what may be claimed. Realizes UJ-1 and UJ-2.

#### FR-71: Sales Playbook
The drafting, Reply classification, Reply Draft, and Call Prep Sheet prompts follow the Sales Playbook; each Draft records its version (FR-20).
- Law and platform rules (§5.1) override the Sales Playbook. Where this PRD and Sales Playbook v0.1.0 differ, this PRD governs until the Sales Playbook is updated; notably, Step 1 asks about relevance, not for "a 20-minute look" (FR-4).

#### FR-72: Approved Company Facts
The Operator maintains Approved Company Facts in the dashboard: the only source of claims about Nextron, its case studies, and regulations (FR-20, FR-21). Each entry holds the exact wording; type (company, case study, Offer, regulation, or statistic); source URL; allowed Segments and Markets; any metric, only once the client confirms it (§6.4); and the date last checked.
- It's seeded from Sales Playbook §6. The Legal AI entry states the client was a legal-tech venture: no Draft may claim Nextron built it "for a law firm". Wish Wheels is an automotive retail and services business, not a distributor.
- Regulatory entries carry their Market (ABA Formal Opinion 512: US; SRA notice: UK) and the source's exact scope (such as Opinion 512's informed-consent point concerning self-learning tools), and are never cited to the other Market.

#### FR-73: AI Test Sets
The engine keeps two AI Test Sets and runs them whenever a prompt, model setting, or Sales Playbook version changes. A failing version can't go active. Results are stored with the version tested.
- *Grounding set:* at least 30 Drafts with known answers, including a fixture for every banned pattern in Sales Playbook §4 and FR-21, and planted unsupported claims about the Lead, Nextron, and regulations. Pass: 100% of banned patterns and at least 95% of planted unsupported claims caught.
- *Reply set:* at least 50 labeled Replies covering every Reply Class, including opt-outs in varied wording. Pass: 100% recall on *Unsubscribe* and at least 90% of all classifications correct.

## 5. Cross-cutting requirements

### 5.1 Compliance
- **CAN-SPAM (US):** truthful From and subject (FR-21), postal address (FR-23), working opt-out (FR-23, FR-41, FR-70), honored immediately (legal limit: 10 business days). Solicitation label: OQ-8.
- **PECR and UK GDPR (UK):** corporate subscribers only (FR-2, FR-12, FR-18); Sender identified and an opt-out in every email, and a privacy notice at first contact (FR-23); legitimate-interest basis and assessment (FR-3, FR-51); erasure, export, and retention (FR-52, FR-53).
- **Mailbox-provider sender rules (Gmail, Yahoo, Microsoft):** authenticated, aligned Sending Domains (FR-64); one-click unsubscribe (FR-70).
- **Blocked Markets:** no cold email outside the US and UK (FR-2).
- **Platform terms:** no LinkedIn or Instagram automation or scraping (FR-49); owner-only LinkedIn Tasks (FR-48); Channel Permissions (FR-24, FR-47); Places storage terms (FR-5).

### 5.2 Cost
Total running cost stays under $100 a month (SM-9), enforced by FR-57.

| Item | Month 1 | Month 2 on |
|---|---|---|
| Google Workspace, 3 Sending Inboxes [ASSUMPTION: monthly billing at about $8.40 each until the domain proves stable] | ~$25 | ~$25 |
| Sending Domains | ~$2 | ~$2 |
| Claude API (FR-57 budget) | up to $20 | up to $20 |
| Hunter Starter, 2,000 credits a month [ASSUMPTION: $34 a month billed annually, $49 billed monthly] | none | $34–49 |
| Places, Companies House, ATS boards, Telegram, Cal.com, Unsubscribe Endpoint | free tiers | free tiers |
| **Total** | **~$35–50** | **~$70–85 expected (Hunter billed annually); at most ~$96** |

### 5.3 Reliability
- No duplicate sends, Daily Cap overruns, or Steps outside a Send Window (FR-34, FR-35).
- Background jobs restart when the PC boots or wakes; the engine recovers unaided from sleep, reboot, or network loss (FR-36).
- Sending, polling, and alerts run only while the PC is on [ASSUMPTION: the PC stays on from the start of the UK morning Send Window until 17:00 US Pacific time on weekdays; Mac has confirmed mornings only]. While it's off, Gmail notifications on the Sender's phone cover hot replies; Manual Replies are logged on the next poll (FR-65).
- The local data store is backed up daily to a second location on the PC and, encrypted, off the PC (an external drive or cloud folder the Operator chooses). The off-PC copy is mandatory for the Suppression List and Provenance Records. A monthly test restore reports to the health panel (FR-58).

### 5.4 Security and privacy
- All data stays on the local PC except calls to the named external APIs, the Unsubscribe Endpoint (opaque tokens and request times only), and the encrypted off-PC backup (§5.3).
- Credentials and OAuth tokens are encrypted at rest or kept in Windows Credential Manager, never in plain-text config files or logs.
- The dashboard listens on localhost only, with no remote access in v1. The Sender works from alerts, email, and the Gmail app.

### 5.5 Deliverability
- Cold email goes only from Sending Domains that pass FR-64, relayed by the mailbox provider.
- Plain text only; no open or link tracking in v1, and neither can be turned on.

### 5.6 AI quality
- Grounding: FR-20, FR-21, FR-72. AI-extracted facts are labeled as extracted and link to their Evidence.
- Versioning and test gates (FR-20, FR-73) let a reply-rate change be traced to a prompt, model, or Sales Playbook version.

## 6. MVP scope

### 6.1 In scope
All of §4, for the *Law-PrivateAI* and *Ops-Automation* Segments, in the US and UK Markets, on one Windows PC, with one Operator role, built in the Release Slices of §6.3.

### 6.2 Out of scope for MVP
- Warm-up email exchange automation; use manual warm-up or a paid tool (OQ-1).
- Sending from a cloud server or VPS. *Revisit if PC uptime proves unreliable.*
- Remote dashboard access and multi-user roles.
- Paid lead databases; Hunter Starter (FR-14) is the only paid data service.
- Google Places review content as a Signal: the storage terms (FR-5) don't allow keeping it as Evidence, and a quoted complaint is a poor opener.
- The SRA register check for UK law firms: API availability is unconfirmed.
- Seed-mailbox inbox-placement testing, and automatic booking detection from Cal.com. *Revisit after Slice 3.*
- More Segments (CPA, RIA, clinics, real estate) and Markets (Gulf, India, EU, Canada). FR-1 and FR-2 must allow adding Segments and simple Markets without code changes; Markets with consent or conditional rules will need code.
- Automated calling, SMS, and WhatsApp Business API sending.
- A personalized "opportunity snapshot" page or PDF per Lead. `[NOTE FOR PM]`: a strong v2 conversion idea.

### 6.3 Release Slices
The engine is built in three slices. Slice 1 must be ready to send when the first Sending Inboxes reach 14 days of recorded warm-up (FR-33), so the inboxes are bought first (§6.4) and warm while Slice 1 is built. Every slice records the events FR-54 and FR-55 later report.

| Slice | Goal | FRs |
|---|---|---|
| **1. First Send** | Hand-picked Leads (CSV import, Signal Inbox) are researched from their websites, drafted, approved, sent, and answered, with suppression and compliance in place | FR-1 to FR-4 as shipped configuration, without editing screens; FR-7 to FR-16 (FR-12 includes the Companies House lookup for a known Lead); FR-18; FR-19's capacity limit, with Leads picked by the Operator (FR-59); FR-20 to FR-23; FR-25 to FR-45; FR-49 to FR-52; FR-56 to FR-59; FR-63 to FR-67; FR-70 to FR-73 |
| **2. Automated supply** | The engine discovers, scores, tiers, and re-checks Leads itself; calls are prepared and recorded | FR-5; FR-6; FR-17; FR-19's automatic selection; FR-53; FR-60 to FR-62; FR-68; FR-69 |
| **3. Assist and insight** | LinkedIn assist, full analytics, and pipeline polish | FR-1 to FR-4 editing screens; FR-24; FR-46 to FR-48; FR-54; FR-55 |

- FR-68 and FR-69 must ship before the first booked call is likely, about 2 weeks after the first send. Until then, Furqaan uses Sales Playbook §7 by hand, and the Operator records outcomes in Lead notes.
- FR-53 must ship before the first deletion falls due, 6 months after the first discovery.

### 6.4 Launch prerequisites
Business setup outside the engine, from the brief and the reviews.

| Item | Owner | Needed by | Blocks the first send? |
|---|---|---|---|
| Sending Domain(s) and 3 Sending Inboxes bought, with SPF, DKIM, and DMARC set, and warm-up started the same day. Buy now; the warm-up method (OQ-1) doesn't block the purchase | Mac | Now | Yes (14-day warm-up) |
| Every Sending Inbox in the Gmail app on Furqaan's phone, with notifications on | Furqaan | First send | No, but hot replies then wait for the PC |
| Booking link (a Cal.com event type) on nextronai.com, replacing the mailto link; weekly availability set for proposed slots | Furqaan | First send | Yes (FR-43) |
| Business postal address (OQ-2) | Furqaan | First send | Yes (FR-23) |
| Privacy notice page on nextronai.com | Mac | First UK send | Yes, for UK (FR-23) |
| Legitimate-interests assessment for each UK Campaign | Mac | First UK send | Yes, for UK (FR-3) |
| 1–2 case studies rewritten with client-confirmed numbers and entered in Approved Company Facts | Furqaan | First send | No, but proof stays weak without them |
| Furqaan's LinkedIn profile updated; Nextron company page | Furqaan | First send | No |
| Telegram bot and alert chat | Furqaan | First send | No (email alerts suffice) |
| Hunter Starter subscription | Mac | Month 2, at full ramp | No |

## 7. Non-goals

- Not a mass-email tool: volume is capped by design, and more volume isn't a goal.
- Not a LinkedIn or Instagram automation or scraping tool, under any configuration.
- Not a general CRM: the pipeline only tracks outreach to signed engagements.
- Not autonomous: no outbound message of any kind goes out unless the Operator approved it or the Sender wrote and sent it himself.
- Not a product for other companies in v1.

## 8. Success metrics

**Primary**
- **SM-1: Paid engagements.** Signed audits or builds: the first within 90 days of the first cold email, then at least 1 a month. Validates the whole system.
- **SM-2: Calls booked.** From engine-sourced Leads: 4+ a month from month 2, which at full capacity needs above-average Tier A conversion (addendum §4). Validates FR-42, FR-43, FR-65, FR-68.

**Secondary** (rates are per Contact contacted)
- **SM-3: Positive reply rate.** *Interested*, *Question*, and *Referral* Replies: 1.5% or more, reported by Lead Tier. Validates FR-11, FR-20, FR-62.
- **SM-4: Reply rate.** Human Replies except *Out of Office* and *Unsubscribe* (which covers "no" opt-outs): 4% or more. Validates FR-4, FR-20, FR-22.
- **SM-5: Deliverability.** Bounce rate under 2%; spam complaints under 0.1% (from Postmaster Tools when it has data, and from spam-accusation Replies); zero blocklistings. Validates FR-15, FR-16, FR-33, FR-38, FR-64.
- **SM-6: Operator time.** Median daily approval time of 30 minutes or less at full volume. Validates FR-29, FR-30.
- **SM-7: Draft acceptance.** At least 80% of Drafts approved without a regenerate or major edit (FR-55). Validates FR-20, FR-21, FR-71.
- **SM-8: Hot Lead response time.** Median of 1 business hour or less from a Hot Lead Reply's arrival to the first response sent (Manual Reply or approved Reply Draft), in the Sender's configured working hours (default 09:00–18:00 weekdays, his timezone). A Reply outside them starts the clock at the next working hour. Validates FR-42, FR-43, FR-65.
- **SM-9: Running cost.** Under $100 a month. Validates FR-57.
- **SM-10: Opt-out rate.** *Unsubscribe* Replies plus one-click and mailto unsubscribes, reported by Campaign and Variant, separately from SM-4. No target; investigate any Variant at more than twice its Campaign's rate. Validates FR-41, FR-70.
- **SM-11: Event-to-send time.** From an Event's first-seen time to sending the Step 1 citing it: median of 2 business days or less for Tier A, and 1 or less for Signal Inbox items. Validates FR-8, FR-19, FR-26.

**Counter-metrics (don't optimize)**
- **SM-C1: Emails sent.** More volume isn't success; chasing it burns domains and the brand. Counterbalances SM-2.
- **SM-C2: Leads discovered.** A big database of poor-fit Leads is waste. Counterbalances SM-3.
- **SM-C3: Raw reply rate, opt-outs included.** Provocative copy can raise replies through angry or "stop" responses, so SM-4 excludes opt-outs; judge by the positive reply rate. Counterbalances SM-4.
- **SM-C4: Approval speed.** Rubber-stamping defeats the human checkpoint; watch the rejection rate. Counterbalances SM-6.
- **SM-C5: Capacity used.** Unused capacity from a signal shortfall is fine; filling it with hand-picked Tier C Leads isn't. Watch the generic-Draft share (FR-54). Counterbalances SM-2.

**Decision checkpoints**
- After about 300 contacted Contacts in a Campaign, a positive reply rate below 0.5% means replacing the Variant; after two failed Variants, review the Segment or Offer.
- After 10 held calls (FR-69), fewer than 2 audits accepted means reviewing the Offer's price and scope before the next Variant.
- At 90 days after the first send with no paid engagement, Mac and Furqaan review the Segments, Offer, and Signal sources. Raising volume isn't an option (SM-C1).

## 9. Open questions

- **OQ-1: Warm-up method.** Manual, a paid tool, or engine-run in v2? It doesn't block buying the inboxes; manual warm-up can start the day they're live. *Owner: Mac; first week of warm-up.*
- **OQ-2: Business postal address** for the footer: San Jose HQ or a virtual mailbox? *Owner: Furqaan; before the first send.*
- **OQ-3: Google Places storage terms.** What may be kept, and for how long? *Owner: architecture step.*
- **OQ-4: Approval load.** If approval still takes more than 30 minutes a day after 2 weeks at full volume, despite FR-29 and FR-30, what gives: fewer new Contacts a day, or per-email approval (Mac's call)? *Owner: Mac; after 2 weeks at full volume.*
- **OQ-5: LinkedIn account.** Whose account carries out LinkedIn Tasks, and is it free or Premium? Default: Furqaan's, about 10 minutes a day from his phone. *Owner: Furqaan and Mac; before Slice 3.*
- **OQ-6: Unsubscribe Endpoint host.** Google Apps Script or a Cloudflare Worker? *Owner: architecture step; before the first send.*
- **OQ-7: Job-aggregator API (FR-60).** Which one, if any, and do its quota and terms allow Lead discovery? *Owner: architecture step; before Slice 2.*
- **OQ-8: Solicitation label (CAN-SPAM).** Must each US email identify itself as a solicitation, and in what words? *Owner: Mac, with counsel; before the first US send.*

## 10. Assumptions index

- §3 Offer: prices validated on the first 10 held calls; deliberately below research benchmarks.
- §4.2 FR-5: only the place ID is kept long-term; name and website are held until the Lead's website replaces them.
- §4.2 FR-60: a free or low-cost job-aggregator API exists; quota unverified.
- §4.3 FR-7: about 20–30% of enriched Leads have a live Tier 1–2 Event found automatically.
- §4.4 FR-14: Prospeo's free tier allows API use.
- §4.4 FR-15: about 40% of Primary Contacts have a Published address.
- §4.5 FR-17: Lead Score weights Fit and Signal 50/50 (configurable).
- §4.5 FR-19: about 22 new Contacts a weekday at full ramp from month 2; about 8–12 in month 1.
- §4.9 FR-43: no calendar integration in v1.
- §4.11 FR-48: free LinkedIn accounts allow only a few personalized notes a month, up to 200 characters each.
- §4.12 FR-53: retention of 6 months after discovery (never emailed) or 12 months after the last Step (no engagement).
- §5.2: Google Workspace billed monthly at about $8.40 per inbox.
- §5.2: Hunter Starter at $34 a month billed annually, or $49 billed monthly.
- §5.3: the PC stays on from the UK morning Send Window until 17:00 US Pacific time on weekdays.

## 11. Risks

| Risk | Mitigation | Residual risk |
|---|---|---|
| Warm-up delays the first send (brief) | Inboxes bought first (§6.4); Slice 1 ready at 14 days of warm-up (§6.3); ramp starts at the warm level (FR-33) | First send about 2–4 weeks after purchase |
| Credibility: no clients or metrics (brief) | Claims only from Approved Company Facts (FR-72); regulatory angle in Law Step 2 (FR-4); a fixed-fee, credited audit as first step (Offer); case studies with numbers (§6.4) | Proof stays thin until a client confirms numbers |
| PC uptime (brief) | Exactly-once sending and catch-up (FR-35, FR-36); approval expiry (FR-31); Manual Replies from the phone (FR-65) | No sends or alerts while the PC is off; Gmail notifications are the fallback |
| Verification limits raise bounces (brief) | Address Classes (FR-15); Hunter Starter in month 2; bounce thresholds (FR-38) | Month 1 limited to about 8–12 new Contacts a weekday |
| AI accuracy: invented claims, missed opt-outs (brief) | Grounding check (FR-21) and Approved Company Facts (FR-72), gated by AI Test Sets (FR-73); rule-based opt-out check (FR-40); every email approved | Small test sets; rubber-stamping (SM-C4) |
| Thin automated Event supply | Signal Inbox (FR-8); re-checks (FR-61); optional aggregator (FR-60); shortfall shown, not filled (FR-19, SM-C5) | Inbox capacity may go unused |
| Approval load and stalls, since every email is approved | Fast cards (FR-29); batch follow-ups (FR-30); stall handling and away dates (FR-31) | Follow-ups slip when nobody approves for days (OQ-4) |
| Unsubscribe Endpoint outage | Mailto and reply opt-outs remain (FR-41); polling alert (FR-70); requests kept until polled | Low: far inside CAN-SPAM's 10 business days |
| Manual Replies bypass the grounding check | Reply Draft offered as starting text (FR-42); suppression alert (FR-65) | Off-script claims are the Sender's responsibility |
| LinkedIn restricts the account | Owner-only operation; 10 a day and 100 a week per account (FR-48, FR-49) | LinkedIn can change limits without notice |
| Offer priced below research benchmarks | Validated on the first 10 held calls (FR-69, §8) | Early deals may be underpriced |
| Spam-foldering at Microsoft 365 buyers is invisible without open tracking | Authentication checks (FR-64); bounce and complaint guards (FR-38); reply rate per Sending Inbox (FR-54) | Seed-placement tests deferred (§6.2) |
| AI spend exceeds the budget | FR-57 stops non-essential work; the architecture step resizes prompts and models | Discovery slows late in the month |

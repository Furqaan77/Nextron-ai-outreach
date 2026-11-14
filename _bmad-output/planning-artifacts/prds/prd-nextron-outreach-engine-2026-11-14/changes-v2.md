---
title: "PRD Changes v2: Nextron Outreach Engine"
created: 2026-11-14
files_changed:
  - prd.md
  - addendum.md
---

# PRD Changes v2: Nextron Outreach Engine

This revision applies the 18 consolidated changes and the reviewers' high findings to `prd.md` and `addendum.md`. Mac's decisions in `.memlog.md` were treated as binding. In particular, every email, follow-ups included, is still approved individually; the PRD now handles the effects of that decision instead of reverting it.

**ID summary**
- **FRs:** 15 added (FR-59 to FR-73). None retired. FR-7 moved from §4.2 to §4.3 with its ID kept. FR-1 to FR-58 keep their IDs.
- **UJs:** UJ-1 to UJ-5 kept. UJ-2 rewritten; UJ-4 and UJ-5 cut to one line each.
- **SMs:** SM-1 to SM-9 and SM-C1 to SM-C4 kept. SM-4, SM-8 and SM-C3 redefined. Added SM-10 (opt-out rate), SM-11 (Event-to-send time) and SM-C5 (capacity used).
- **New sections:** §4.15 Sales Playbook & Approved Company Facts, §6.3 Release Slices, §6.4 Launch Prerequisites, §11 Risks, and Decision checkpoints in §8.
- **Open Questions renumbered:** old OQ-1 → 1 (reworded: no longer blocks the inbox purchase); OQ-2 → 2; OQ-5 → 3; OQ-7 → 4 (reworded). Old OQ-3 (booking link), OQ-4 (Telegram) and OQ-6 (UK LIA) were setup tasks and moved to §6.4. New: OQ-5 (whose LinkedIn account), OQ-6 (Unsubscribe Endpoint host), OQ-7 (job-aggregator API), OQ-8 (CAN-SPAM solicitation label).

## (a) The 18 required changes

| # | Change | Where implemented |
|---|---|---|
| 1 | Email volume: *Published* class, verified *Guessed*, Hunter Starter in month 2, recomputed capacity and cost | Glossary (Address Class, Eligibility); FR-14 (Domain Search, Hunter Starter in month 2); FR-15 rewritten (Published, Guessed, Verified, Invalid; 30% *Likely* cap and its assumption removed; new 40%-Published assumption); FR-16; FR-18; FR-19 (new capacity assumption: about 22 a weekday from month 2, 8–12 in month 1; new-Contact ceiling = capacity ÷ Steps); FR-57 (credits metered per operation); §5.2 cost table (month 1 ~$35–50; month 2 on ~$70–85, at most ~$96); §6.4; addendum §4, which rewrites the capacity model and removes the "most addresses will be *Likely*" contradiction |
| 2 | Hot replies from Furqaan's phone | Glossary (Sending Inbox, Manual Reply); UJ-2 rewritten; FR-39 (Sent folder polled every 2 minutes); FR-42 (alerts carry the Reply Draft, booking link and two slots); FR-43 (two slots, ≤90 words, superseded by a Manual Reply); new FR-65 (Manual Reply detection: logs it, counts it as approval, updates the Pipeline Stage, stops SM-8's clock); SM-8 (median ≤1 business hour, in the Sender's configured working hours); §7 "Not autonomous" amended; §5.3 (PC uptime assumption and Gmail fallback); §6.4 (inboxes in his Gmail app); addendum §3 |
| 3 | One-click unsubscribe (RFC 8058) | New FR-70 (List-Unsubscribe + List-Unsubscribe-Post, free hosted endpoint, polled every 5 minutes, only off-PC component, health alert); FR-41 (reply opt-out kept, all routes); FR-37; Glossary (Unsubscribe Endpoint); §5.1; §5.4; OQ-6 (host left to architecture). Removed: the FR-41 mailto-only assumption, the §6.2 "5,000 a day" item and the §10 entry. Addendum §1, §2 and §3 |
| 4 | Sales Playbook and Approved Company Facts | New §4.15: FR-71 (prompts follow the versioned Playbook; each Draft records its version; this PRD governs where Playbook v0.1.0 differs), FR-72 (Approved Company Facts library in the dashboard; the only source of claims about Nextron, case studies and regulations; Legal AI = legal-tech venture client, never "built for a law firm"; regulatory entries tied to their Market). FR-20 and FR-21 (grounding check covers Nextron and regulatory claims as strictly as Lead claims); FR-27; Glossary; §5.6; case studies with numbers in §6.4 |
| 5 | Signals: Events and Attributes, observational Evidence, default Signal list, Lead Tiers | Glossary (Signal, Event, Attribute, Signal Tier, Evidence types Quote/Absence/Count, Lead Tier, Fit/Signal Score); FR-1 (default Signal list per Segment from Playbook §1, plus disqualifiers); FR-11 (Evidence types, event date, decay by Signal Tier, hedged Absence wording, score-only Attributes); FR-17; new FR-62 (Tier A/B/C; Tier C never auto-selected, manual pick allowed); FR-19 (Lead Score minimum alone never admits Tier C); FR-22 |
| 6 | Re-check and re-contact | New FR-61 (Eligible Leads not in a Sequence re-enriched every 30 days, careers pages every 7; Not Now Leads on their chosen date); new FR-63 (restart only after ≥90 days AND a new Event; never after Not Interested or Unsubscribe); FR-41 (Not Interested suppresses the address); FR-18 |
| 7 | Job feeds as enrichment | FR-7 moved to §4.3 and rewritten as hiring-signal enrichment (careers pages plus ATS boards detected on the Lead's site, posting status tracked); new FR-60 (optional job-aggregator discovery, off by default, `[ASSUMPTION: … quota is unverified]`); FR-8 (multi-item paste for manual hiring searches); OQ-7; addendum §2 |
| 8 | Sequence content | FR-4 rewritten with the default Step briefs table from Playbook §3: Step 1 asks about relevance ("Worth a look?"), not for a meeting; Step 4 is a respectful close; content, ask and word limit defined for every Step; no price or audit in any Step; weekend rule |
| 9 | AI quality bars | New FR-73 (grounding set ≥30 Drafts including every Playbook §4 banned pattern, 100% caught, ≥95% of planted claims; Reply set ≥50 labelled Replies, 100% *Unsubscribe* recall, ≥90% overall); FR-40 (uncertain → Sequence paused and Reply flagged; rule-based opt-out check); FR-37; Glossary Reply Class adds *Data Question* (not hot); FR-66 (its Reply Draft states the source from the Provenance Record) |
| 10 | Reply Drafts for objections, Not Now and Referral | Glossary Reply Class adds *Objection* (typed by Playbook §5); new FR-66 (Objection, Not Now with captured date, Data Question); new FR-67 (Referral hand-off: new Contact with the referrer noted in the Provenance Record; referrer named only with consent) |
| 11 | Call prep and call outcomes | New FR-68 (Call Prep Sheet per Hot Lead, Playbook §7, emailed on *Call Booked*); new FR-69 (Call Outcome: held/no-show, outcome, audit proposed, objections, price reaction; no-show rebooking Draft; Offer report after 10 held calls); Pipeline Stage adds *Call Held* and *Not Now*; §8 Decision checkpoints; Offer assumption now cites FR-69 |
| 12 | Metrics | SM-4 now excludes *Unsubscribe* (including "no" opt-outs); new SM-10 reports opt-outs separately; SM-C3 redefined so it doesn't counterbalance itself; new counter-metric SM-C5 (capacity used versus hand-picked Tier C fill); FR-54 dimensions and measures extended |
| 13 | WhatsApp and Instagram | Glossary (Channel Permission); FR-24 (only if the prospect started contact on that channel or explicitly opted in to WhatsApp; an email reply, signature or website number doesn't qualify; Instagram 24-hour window); FR-47; §5.1 |
| 14 | Sending safety | New FR-64 (SPF, DKIM, DMARC alignment checked on connect and daily; cold sends blocked on failure); FR-32 (all nextronai.com subdomains refused; >3 inboxes per domain warned; personal From only); FR-33 (warm-up record; cold sends blocked until 14 days of recorded warm-up; ramp starts at the actual warm level; warm-up tool sends count toward a total ceiling); FR-58; §6.4 and OQ-1 (inbox purchase doesn't wait for the warm-up decision) |
| 15 | LinkedIn | FR-48 rewritten (only the account owner carries out Tasks, no account sharing; default Furqaan, 10 Tasks a weekday, ~10 minutes from his phone; notes optional with an assumption on free-account limits; 100-a-week cap per account; search hint when no URL); FR-24 (note ≤200 characters, optional); Glossary LinkedIn Task; UJ-3; new OQ-5 |
| 16 | Build order | New §6.3 Release Slices (Slice 1 First Send, Slice 2 automated supply, Slice 3 assist and insight), with all 73 FRs mapped; new FR-59 (CSV import, with imported emails treated as *Guessed*); FR-19 notes manual selection until Slice 2 |
| 17 | Carry-overs from the brief | New §6.4 Launch Prerequisites (sending domain and inboxes, booking link on the website, postal address, privacy notice, case studies with numbers, Furqaan's LinkedIn, UK LIA, Telegram, Hunter in month 2); new §11 Risks (the brief's 5 risks plus 8 new); §4.6 (Furqaan's voice defined by Playbook §2); §4.7 states the approval-model change from the brief, the FR-30 batch approval that protects the 30-minute target, and FR-31's stall handling (delays count from the previous actual send, "stalled" flag at 7 days, away dates) |
| 18 | Rubric fixes | Cut repetition (see below); the 300 ms next-card limit moved into FR-29 and removed from addendum §3; FR-56's credential list moved to addendum §2; addendum still states "recommendations and constraints, not decisions" |

**Repetition cut (change 18):** about 500 words. About 250 came from the sections the rubric named: §0 shrunk to two sentences; §2.2 shrunk to one line; UJ-4 and UJ-5 cut to one line each; §5.2's restatement replaced by the cost table; §5.5 no longer restates FR-38 and SM-5; FR-56's list moved to the addendum; the §4.4, §4.5 and §4.14 descriptions trimmed. About another 250 came from duplication the new content would otherwise have added: glossary definitions repeated in FRs, the AI-test gate stated in four places, long feature descriptions, and UJ-2.

## (b) High findings from the four reviews

### review-rubric.md
The file has no finding IDs, so findings are cited by section and order.

| Finding | Resolution |
|---|---|
| Decision-readiness, high 1: the hot-reply path depends on PC uptime and Mac | Resolved by change 2: FR-42, FR-43, FR-65, UJ-2, SM-8 (clock and business hours defined). §5.3 states the PC uptime needed for US afternoons as an assumption, with Gmail notifications as the fallback. Absence rule: FR-31 away dates; hot replies don't wait for the Operator |
| Decision-readiness, high 2: the verification budget and the *Likely* cap allow about 40 sends a day | Resolved by change 1: FR-15 Address Classes, FR-19, §5.2, addendum §4 |
| Strategic coherence, high 1: no first-send slice | Resolved: §6.3 Release Slices with an FR map; Slice 1 is timed to 14 days of recorded warm-up |
| Strategic coherence, high 2: hiring-Signal sources can't feed the thesis | Resolved: FR-7 moved to §4.3 as enrichment; `[ASSUMPTION]` on the share of Leads with an automatic Event; FR-60 optional aggregator with its terms question in OQ-7 |
| Done-ness, high 1: AI-judgment gates have no acceptance bar | Resolved: FR-73 pass marks; safe defaults in FR-40 (rule-based opt-out check overrides the classifier; uncertain pauses the Sequence and still alerts when it might be hot) |
| Done-ness, high 2: Evidence can't hold absences or counts | Resolved: Evidence types (Quote, Absence, Count) in the Glossary and FR-11; hedged Absence wording enforced by FR-21; score-only Attributes |
| Done-ness, high 3: nothing governs claims about Nextron | Resolved: FR-72 Approved Company Facts, FR-21, Glossary; case-study metrics only once a client confirms them |

### reconcile-brief.md

| ID | Resolution |
|---|---|
| R-01 one-click unsubscribe dropped | Resolved by change 3 (option a): FR-70; the assumption is removed |
| R-02 approval granularity contradicts the brief | Mac's decision kept, as it is binding. Knock-ons resolved: §4.7 states the change from the brief; FR-29 (300 ms), FR-30 (batch follow-ups in v1, with what the compact list shows), FR-31 (stalls, delays from actual send, away dates); OQ-4 reworded. The Sequence-level approval alternative was rejected because it conflicts with Mac's decision. **Deferred:** amending brief.md L18/L94 and logging the change in the brief's memlog, because this pass may edit only prd.md and addendum.md |
| R-03 hand-off to Furqaan weakened | Resolved by change 2: alerts carry the Reply Draft, booking link and slots; Manual Reply (FR-65); the dashboard notification is qualified as "at the PC"; SM-8 is tied to FR-42, FR-43 and FR-65 |
| R-04 prerequisites and inbox-purchase timing dropped | Resolved: §6.4 Launch Prerequisites with owner, needed-by and blocking columns; OQ-1 no longer gates the purchase |
| N-01 *Likely* cap and credits allow about 10 new Contacts a day | Resolved by change 1 (options a and c together) |
| N-02 Warm-up Ramp re-ramps warmed inboxes | Resolved: FR-33 starts at the recorded warm level after 14 days of recorded warm-up. The "finish within 4 weeks" suggestion was not adopted: the ramp still rises about 5 a week, but from the warm level rather than 5 |

### reconcile-research.md

| ID | Resolution |
|---|---|
| R-01 *Likely* cap can't be met | Resolved by change 1; credits metered per operation (FR-57); Domain Search once per domain (FR-14); Prospeo API tagged `[ASSUMPTION]` |
| R-02 no SPF/DKIM/DMARC check; subdomains; inboxes per domain | Resolved: FR-64; FR-32 (subdomains refused, >3 inboxes warned); relaying through the provider stated in §4.8, §5.5 and addendum §2 |
| R-03 one-click unsubscribe dropped | Resolved: FR-70; the wrong-unit "5,000 a day per inbox" trigger is removed |
| R-04 WhatsApp and Instagram rules weaker than the research | Resolved: Channel Permission (Glossary, FR-24, FR-47); Instagram 24-hour window. Not adopted: the 7-day comment-reply window, since v1 drafts no comment replies |
| R-05 cold sends can start before warm-up | Resolved: FR-33 (14-day minimum; warm-up tool sends count toward a total daily ceiling of 50) |
| R-06 no default Signal list per Segment | Resolved: FR-1 table (from Playbook §1), news and press pages in FR-10, Dynamics GP row, disqualifiers in FR-1. **Deferred:** per-Signal keyword lists and negative Signals beyond the disqualifiers, which belong in Playbook v0.2.0 and Segment configuration |
| R-07 no case-study or proof library | Resolved: FR-72 (with the Legal AI and Wish Wheels wording), FR-21, FR-4 (Steps 2–3 draw on Approved Company Facts) |

### review-sales.md

| ID | Resolution |
|---|---|
| TOP 1 trigger-first selection | Resolved: Events and Attributes, Lead Tiers (FR-62), Tier-ordered selection with signal shortfall (FR-19), decay by Signal Tier (FR-11), Primary Contact by Event role (FR-13), generic fallback only for hand-picked Tier C (FR-22). Differs from the proposal: Tier B means a live Tier 2 Event (per required change 5), not "two Conditions". **Deferred:** a per-Signal "angle" column, which belongs in Playbook v0.2.0 |
| TOP 2 Step briefs, CTA ladder, Claims Library | Resolved: FR-4 Step briefs and asks; FR-72 (the Claims Library is implemented as Approved Company Facts); FR-21; price only in Reply Drafts (FR-4, FR-43). Partly: the Step 3 ask is an offer to send more detail by reply, rather than a prepared resource (not added as a prerequisite) |
| TOP 3 hot-reply path | Resolved by change 2. **Deferred:** a 90th-percentile bound for SM-8 (only the median was required) |
| F-1 hiring feeds barely reach the ICP | Resolved: FR-7 (careers pages plus detected ATS boards; other hosts after a terms check); FR-8 multi-item paste; hiring Events per 100 Leads crawled (FR-54) |
| F-2 no re-checks or re-contact rules | Resolved: FR-61, FR-63. **Deferred:** a second Contact at Ops Tier A Leads after 21 days, which conflicts with the one-active-Contact rule and adds complaint risk; revisit after Slice 2 data |
| F-3 no speed-to-signal target or freshness check | Resolved: FR-8 (processed on submission, Draft within 15 minutes); FR-26 queue order; FR-31 job-posting re-check; SM-11 |
| F-11 Reply Classes miss key replies | Resolved: *Objection* and *Data Question* added; a left-company notice naming a successor is a Referral (FR-67); *Not Now* captures a date. **Deferred:** separate *Wrong Person* and *Left Company* classes, to keep the classifier small enough to meet FR-73's 90% bar with a 50-reply set |
| F-12 objections get no Reply Drafts | Resolved: FR-66, using Playbook §5 patterns. **Deferred:** per-Segment objection lists, which belong in Playbook v0.2.0 |
| F-13 Referrals not turned into introductions | Resolved: FR-67. Not adopted: a two-Step Referral Sequence; the referred Contact follows the Campaign's Sequence |
| F-16 LinkedIn account, notes and order | Resolved by change 15; Task order in FR-48. **Deferred:** drafted comments on Signal Inbox posts |
| F-18 no discovery-call preparation | Resolved: FR-68. **Deferred:** automatic booking detection (§6.2); the Operator still moves Leads to *Call Booked* |
| F-19 call outcomes not captured | Resolved: FR-69, *Call Held* stage, no-show rebooking Draft, §8 checkpoint. **Deferred:** a pre-call confirmation email, since Cal.com sends its own confirmations and reminders |
| F-20 funnel can't show what's working | Resolved: FR-54 dimensions (Tier, Signal type and source, role, inbox, Step of first Reply) and measures (opt-out rate, generic share, shortfall, SM-8, SM-11, no-shows). **Deferred:** the LinkedIn-touch dimension and open pipeline value, until Slice 3 shows they're needed |

### Medium and low findings also fixed
- **review-rubric.md:** FR-30 confirmed in v1, with the compact list's contents; checkpoints that link metrics to action (§8); FR-38 complaint and blocklist sources; SRA and Places reviews listed in §6.2 with reasons; CAN-SPAM solicitation label raised as OQ-8; mandatory off-PC backup and monthly restore test (§5.3, FR-58); Signal catalog (FR-1); SM-7 made measurable (FR-55, >20% edit); loose qualifiers (FR-16 = 2, FR-28 skip, FR-11 event date, FR-39 2 minutes, FR-4 weekends); Reply Drafts and the Daily Cap (FR-34). Mechanical fixes: one `[ASSUMPTION: …]` tag format; Offer tagged inline; *Interested* stage clarified; Bounce no longer a Reply Class; "No Contact" removed; "deal value" used throughout; FR-51 records the legitimate-interest basis; FR-26/FR-43 queue order aligned; §5.3/§5.4 conflict resolved.
- **reconcile-brief.md:** R-05 to R-11 (Risks, slices, voice, Approved Company Facts, news pages, channels, LinkedIn search hint); R-13 (Offer is fixed-fee and credited; price only on request); R-15 (tracking not configurable); R-16 ("light" manufacturers in §1); N-03 (funnel at capacity in addendum §4 and the SM-2 note); N-04 (2% alert, 3% pause, 50-send minimum); N-06 (AI budget default $20); N-07 (PC uptime assumption); N-08 (2-minute polling); N-11 (10 a weekday). Partly: A-01 (editing screens moved to Slice 3), N-05 (minimum sample of 150 per Variant, but Variants not pooled across Markets).
- **reconcile-research.md:** R-08 (bounce classes, FR-44); R-09; R-10 (Enterprise field, per-request billing, separate quotas, no Maps scraping); R-11; R-12 (headless rendering, widget detection); R-13 (Market per Contact); R-15 (subject check, postal-address preflight, colleague replies, opt-out inside out-of-office); R-17; R-19; R-20 (retention clock from discovery, role-matched people only, UK privacy notice, LIA gate); R-21 (Companies House match rule, free webmail ineligible in the UK); R-22; R-23 (partly; "site readable by crawlers" not added); R-24 (monthly Workspace billing). Partly: R-14 (§6.2 now says consent-only or conditional Markets need code); R-18 (size clues and finance roles added; EDGAR not added). **Deferred:** R-16 (a separate Offer per Segment); the assumption notes the deliberate price gap.
- **review-sales.md:** F-4 (score-only Attributes, no review content); F-5 (disqualifiers); F-6 (subject rules, banned patterns); F-7 (partly: spelling and vocabulary per Market, regulatory facts tied to their Market); F-8 (voice from Playbook §2); F-9 (stale follow-ups regenerated); F-14; F-15; F-17; F-21; F-22 (partly: SM-3 reported by Tier; early warning through the checkpoints). **Deferred:** F-23 (seed-mailbox placement tests; §6.2), F-10 (Variants pooled across Markets).

### Follow-ups outside this pass (files not editable here)
- **Sales Playbook v0.1.0 → v0.2.0:**
  - §2 and §3: the Step 1 ask "Worth a 20-minute look?" now conflicts with FR-4's relevance-only ask.
  - §1: "ABA 512 or SRA notice applies" is listed as a Tier 2 Signal; the PRD treats it as an Approved Company Fact.
  - §6: the Legal AI entry should state that the client was a legal-tech venture, and "PRD feature to be added" should now point to FR-72.
  - Add per-Signal angles and per-Segment objection lists.
- **Brief:** log the approval-model change and amend brief.md L18 and L94 (reconcile-brief R-02).
- **PRD .memlog.md:** the 5k/day mailto assumption is superseded by the one-click decision. The PRD no longer relies on it.

## (c) Final word count
- `prd.md`: **11,963 words** (12,654 by raw `wc -w`, which also counts table pipes and markdown markers). The previous draft was 5,892 (rubric count). The growth comes from the 15 new FRs, four new tables (Signal list, Step briefs, cost, Release Slices), §6.4, §11, Decision checkpoints and new Glossary terms, net of about 500 words of repetition cut.
- `addendum.md`: 1,405 words (1,484 by raw `wc -w`).

## (d) Polish pass

This is the final structure and prose pass (`bmad-review`, lenses: structure and prose), applied directly to `prd.md`. Status stays `draft`; `updated` is 2026-11-14.

- **Word count:** 11,963 → 10,745 by the rubric count (tokens that contain a letter or digit), or 12,670 → 11,440 by raw `wc -w`. This misses the target of about 9,000. The remaining text is testable FR content, the glossary, and tables, and cutting it would change meaning.
- **Kinds of cuts:**
  - Glossary entries that repeated FR detail now point to the FR: Eligibility, Lead Tier, Address Class, Sequence, Variant, Warm-up Ramp, Daily Cap, Sending Domain, AI Test Sets, and LinkedIn Task. FR-11's Evidence-type matching moved into the Evidence entry.
  - FR text that repeated glossary definitions or other FRs now uses cross-references. Examples: Tier C in FR-22, Channel Permission in FR-24, queue order in FR-43, version recording in FR-71, the single claims source in FR-72, and warm-up scope in FR-33.
  - §5.1, §5.3, §5.5, and §5.6 are cut to cross-references. Each keeps what appears only there: the backup and restore rules, the uptime assumption, the tracking ban, and the labeling of AI-extracted facts.
  - Each §4.x description is now 1–3 sentences. §4.7 no longer repeats FR-29 to FR-31 and SM-6.
  - UJ-1 and UJ-2 are shorter, and UJ-4 and UJ-5 are tighter, with every number and time kept.
  - Filler and doubled qualifiers are gone throughout, and §8, §9, and §10 are tighter.
- **Structure:** Every FR is a lead statement plus optional bullets; FR-31 and FR-63 gained lead sentences. Open questions are now labeled OQ-1 to OQ-8, so "OQ-n" references resolve.
- **Prose (Microsoft style, US English):**
  - Headings are in sentence case, with "and" in place of "&".
  - "e.g." is replaced, and lists use the serial comma.
  - US forms: "labeled", "canceled", "underpriced", "PM", and "signs in to".
  - Quantities use "fewer than" and "more than".
  - "Sales Playbook §n" is used consistently.
- **Kept unchanged:**
  - Every FR, UJ, SM, SM-C, and OQ ID, with its meaning.
  - Every number and threshold; a script check found no number from the previous draft missing.
  - All 14 `[ASSUMPTION]` tags, each still indexed in §10.
  - All 46 glossary terms, and all tables.
  - Decisions and scope.
- **Verification:**
  - FR-1 to FR-73 each appear exactly once as a heading.
  - All 333 FR references resolve, and so do all UJ, SM, OQ, and § references.
- **Ambiguity kept as is:** In FR-19 (and FR-26), Tier A is ordered "newest Event first", yet each Tier is also "ranked by Lead Score". The wording stays unchanged because the precedence between the two is a decision for Mac.

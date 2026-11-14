---
title: "Sales Review: Nextron Outreach Engine PRD"
reviewed: prd.md (draft, 2026-11-14)
context: brief.md, brief addendum, PRD addendum
lens: senior outbound sales (signal-based targeting, sequence design, reply and call conversion)
date: 2026-11-14
---

# Sales Review: Nextron Outreach Engine PRD

## Verdict

**Approve for architecture after the three top changes are written into the PRD. The safety work is sound; the selling is underspecified.**

The PRD's compliance, deliverability and grounding requirements are stronger than most commercial outbound setups. Nothing in this review argues for more volume or less control. The gap is the sales core. The PRD says how to send safely, but it doesn't say:
1. which Leads deserve an email today, and why;
2. what the follow-ups say, though they are 68 of the 90 daily emails;
3. how a positive reply becomes a call that is held and well run.

As written, the likely steady state is that most new Contacts get fit-only, near-template copy in the CEO's name, while hot replies wait for someone to sit down at the PC. The three top changes fix that. The other 23 findings can go into the PRD now or be carried into epics.

**The three highest-impact changes**

| | Change | PRD location |
|---|---|---|
| **TOP 1** | **Trigger-first selection.** Split Signals into dated Triggers and lasting Conditions. Tier Leads A/B/C, never auto-send fit-only Leads, and map each Trigger to a person and an angle. | Glossary, FR-1, FR-11, FR-13, FR-17, FR-19, FR-22 |
| **TOP 2** | **Step briefs, CTA ladder and a Claims Library.** Define what Steps 2–4 say. Ask for interest, not a meeting, in cold emails. Check claims about regulation and about Nextron as strictly as claims about the Lead. | FR-4, FR-21, Offer, §9 |
| **TOP 3** | **Hot-reply path.** Let Furqaan answer from his phone, in-thread, within an hour, with a Reply Draft that offers two specific times. | UJ-2, FR-42, FR-43, §7, SM-8 |

**How to read the findings.** Each finding gives where it applies in the PRD (*Where*), the *Problem*, and the *Change*. Quoted text is proposed PRD wording. Priorities are High, Medium or Low.

## Numbers this review uses

- **Capacity:** 90 sends a day is about 22 new Contacts plus 68 follow-ups (PRD addendum §4). Three in four emails are follow-ups.
- **First quarter:** FR-33's default ramp starts at 5 a day per inbox, adds 5 a week and stops at 30. That gives about 4,700 emails in the first 13 weeks of sending, or roughly 1,200–1,400 new Contacts.
- **Replies and calls:** at SM-3's 1.5%, that is about 20 positive replies in the quarter. SM-2 (4 calls a month) needs about half of the positive replies to become calls. SM-1 (a first paid engagement within 90 days) needs one or two of those calls to buy.
- **Test sample:** with 4 Campaigns × 2 Variants, each Campaign-Variant gets about 160 Contacts and 2–3 positive replies in the quarter.

**Every Contact and every positive reply in the first quarter is scarce.** The top changes all follow from that.

## What the PRD gets right: do not change

These are correct. A sales-driven edit could easily weaken them by mistake.

1. **Evidence grounding is a hard gate** (FR-11 "Signals without Evidence are never stored"; FR-20; FR-21 red flag blocks approval; UJ-1 edge case). This is the best brand protection in the document. Extend it (TOP 2); never relax it to "allow some creative personalization".
2. **Step 1 is under 80 words, has no links except the signature and exactly one CTA** (FR-4). This is stricter than both persona frameworks (150 words), and right. Keep 80.
3. **Plain text, no open or link tracking** (§5.5). Don't add tracking "to measure opens". Replies are the metric.
4. **The right measures:** positive reply rate is the judgment metric, rates are counted per contacted Contact rather than per email (FR-54), and counter-metrics SM-C1 to SM-C4 exist. Few outbound plans get this right.
5. **Any human reply stops the Sequence; Out of Office delays it** (FR-37).
6. **Follow-ups come from the same inbox in the same thread** (FR-34). They read as one conversation, and the CEO's thread is intact when the prospect replies.
7. **The Signal Inbox** (FR-8, UJ-3). A human-found LinkedIn pain post is the highest-intent signal this budget can buy. Keep it and make it faster (F-3).
8. **Hot Lead alerts within 5 minutes on three channels, with context** (FR-42), and Reply Drafts at the top of the queue (FR-43).
9. **A narrow ICP, a minimum Lead Score, and one active Contact per Lead** (FR-1, FR-19). The law firm segment has one decision-maker, which is the fastest path to a yes.
10. **Volume caps and pacing:** 30 a day per inbox, a warm-up ramp, randomized gaps, the recipient's local Send Window, and a lookalike domain (FR-32 to FR-34, FR-38, SM-C1). Volume isn't the lever. Keep it capped.
11. **"Reply 'no'" as the opt-out line** (FR-23). It is human and low-friction, and the replies help sender reputation. Only how it is counted needs fixing (F-21).
12. **No LinkedIn automation** (FR-49), **the UK corporate-subscriber gate** (FR-12) and **suppression checked at every stage** (FR-50). These keep the CEO's LinkedIn and the domains alive. Never trade them for reach.
13. **Approval expiry** (FR-31), **rejection reasons** (FR-28, FR-55) and **prompt versioning** (§5.6). Together they form the learning loop.
14. **Four Steps over 14 days** (FR-4). This is the right length for this capacity. Don't lengthen the Sequence; fix what the Steps say (TOP 2).
15. **The audit is credited in full toward a build** (brief, *The Offer*). This is the right risk reversal for an agency with no references.
16. **FR-22 falls back instead of inventing.** That instinct is right. The fix is to gate the fallback (TOP 1), not to remove it.

---

## TOP 1. Make selection trigger-first

**Where:** Glossary *Signal*; FR-1, FR-11, FR-13, FR-17, FR-19, FR-22.

**Problem:**
1. **"Signal" mixes two different things.**
   - A **Trigger** is a dated event that answers "why now". Examples: an intake-specialist posting opened six days ago, a COO's LinkedIn post, a new warehouse.
   - A **Condition** is a lasting attribute that answers "why you". Examples: an immigration practice, 14 attorneys, Dynamics GP mentioned, a contact-form-only website.

   Most of the brief's signal table (addendum C) is Conditions. Only a Trigger lets a cold email answer the buyer's real question: why are you writing to me this week?
2. **Fit can stand in for Signal.** Lead Score is 0.5 × Fit + 0.5 × Signal, and FR-19's minimum is 50, so a perfect-fit Lead with no Signal scores 50 and is auto-selected. FR-22 then sends it a Segment template. Automated signal supply will be thin (F-1), so on a normal day most of the ~22 new Contacts would get near-generic copy. That copy sits in the 1–3% reply band and spends the first quarter's ~1,300 Contacts without teaching anything.
3. **One decay rule fits nothing.** All Signals lose weight after 90 days. A 90-day-old post or job ad is history, while a practice area never decays.
4. **The Contact and the angle ignore the signal.**
   - An AP-clerk posting is the Controller's or Finance Director's pain, not the COO's.
   - A firm hiring for intake needs the intake angle. A firm hiring for document review or knowledge management needs the private document assistant angle.

   FR-13 chooses the Contact by Segment role only, and nothing says which angle a Trigger implies.

**Change:**
> **Glossary (add):** "**Trigger:** a Signal tied to a dated event (a job posting opened, a post published, news announced, a leadership change), with an event date. **Condition:** a Signal describing a lasting attribute (practice area, size, systems used, website capabilities). Conditions feed Fit Score; only Triggers feed Signal Score."

> **FR-1 (add):** "Each Segment has a Signal Playbook with one row per Signal type: class (Trigger or Condition), weight, full-weight period and expiry, target Contact role(s), message angle, the Claims Library proof items it may use, whether it may be cited or is score-only, and an example Step 1 opener. v1 ships with the default Playbook; every field is editable."

> **FR-11 (replace the decay bullet):** "Every Trigger has an event date: the posted, published or announced date, or else the first-seen date. A Trigger keeps full weight for its type's full-weight period and falls to zero at its expiry (default 14 and 45 days). A job-posting Trigger keeps full weight while the posting is live, up to 60 days. Conditions don't decay; they are re-checked whenever the Lead is re-crawled."

> **FR-17 (replace the assumption):** "Each Lead has a Tier. **A:** at least one live Trigger with Evidence, and Fit ≥ 60. **B:** no live Trigger, at least two Lead-specific Conditions with Evidence, and Fit ≥ 70. **C:** everything else. Lead Score ranks Leads within a Tier, never across Tiers. [ASSUMPTION: thresholds are tuned after 300 contacted per Tier.]"

> **FR-19 (replace the first sentence and the first bullet):** "Each day the engine fills new-Sequence capacity in Tier order: every Tier A Lead first, newest Trigger first, then Tier B Leads up to a configured daily number (default 10). Tier C Leads are never selected automatically. They stay on the Watchlist (F-2) until a Trigger appears. Capacity that can't be filled this way stays unused and is shown on the dashboard as 'signal shortfall'."

> **FR-13 (add):** "The Primary Contact is the role the Signal Playbook names for the Lead's strongest live Trigger. If the Lead has no live Trigger, the Segment's decision-maker roles apply. Law-PrivateAI default: the managing partner. Ops-Automation defaults: for AP/AR or finance roles, the Controller, CFO or Finance Director; for dispatch or transport roles, the VP Operations or Head of Transport; for order entry, customer service or inventory roles, the COO or VP Operations; for Leads with fewer than about 100 staff, the owner or CEO."

> **FR-22 (replace):** "Every Draft records its Personalization Level: *Trigger* (opens on a live Trigger), *Observation* (opens on Lead-specific Conditions) or *Segment* (no Lead-specific Evidence). Segment-level Drafts are created only when the Operator selects a Tier C Lead by hand, and they are flagged 'generic'."

**Default Signal Playbook (starting point, editable):**

| Segment | Signal type | Class | Full weight / expiry | Contact | Angle |
|---|---|---|---|---|---|
| Law | Hiring: intake specialist, legal assistant, receptionist | Trigger | While the posting is live, ≤60 d | Managing partner | Intake load: capture and qualify enquiries without adding staff |
| Law | Hiring: document-review paralegal, KM, legal ops, innovation | Trigger | While the posting is live, ≤60 d | Managing partner | Private document assistant on the firm's own data |
| Law | New office, merger, lateral group, new managing partner | Trigger | 21 / 90 d | Managing partner | Growth: standardize workflows and set an AI policy before headcount grows |
| Law | Signal Inbox post about AI, ChatGPT or client confidentiality | Trigger | 7 / 30 d | Post author | Their point, then private AI that keeps client files in-house |
| Law | Document-heavy practice (immigration, PI, family, real estate) | Condition | n/a | n/a | Chooses which document workflow to name |
| Law | Clio, iManage or NetDocuments mentioned | Condition | n/a | n/a | Works with the system they already use |
| Law | Contact form only; no chat or booking | Condition, score-only | n/a | n/a | Never cited (F-4) |
| Ops | Hiring: data entry, order entry, AP/AR clerk, dispatcher, inventory coordinator, "advanced Excel" | Trigger | While the posting is live, ≤60 d | By role (FR-13) | The hours that role spends re-keying; an automation sprint |
| Ops | New facility, expansion, acquisition | Trigger | 30 / 120 d | COO or CEO | Connect systems and scale without more clerical staff |
| Ops | Signal Inbox post about manual work or ERP pain | Trigger | 7 / 30 d | Post author | Their point |
| Ops | Dynamics GP or another legacy ERP | Condition | n/a | COO or Controller | GP end-of-support planning (from the Claims Library) |
| Ops | PDF catalog or emailed order form; no portal | Condition | n/a | COO or sales operations | Order intake automation |

**Why it's top:** this decides who gets the 22 new emails a day and what their first line says. The persona benchmark is 12–25% replies for signal-based outreach against 1–3% for generic. That is optimistic for an unknown agency, but even a 2× gap doubles calls at the same volume and the same risk.

---

## TOP 2. Ship default briefs for Steps 2–4, a CTA ladder and a Claims Library

**Where:** FR-4, FR-20, FR-21, FR-43; Glossary *Offer*; §9; brief *The Offer* and *Prerequisites*; brief addendum D.

**Problem:**
1. **Only Step 1 is specified, though 68 of the 90 daily sends are follow-ups.** FR-4 lets the Operator write a brief per Step but ships none. Addendum D's "each follow-up adds new value" is a guideline, not a requirement. Left alone, a model writes the same pitch four times. Follow-ups commonly produce around half of all replies, so this is where most of the volume either works or burns reputation.
2. **No rule says which rung of the Offer each message asks for.** The brief's Step 1 ask, "worth a 20-minute look?", is a meeting request from an unknown agency in a first email. An interest question such as "is this on your radar?" is easier to answer, and the call comes after the reply. Price and the audit don't belong in cold emails.
3. **Grounding covers claims about the Lead only, not claims about the world or about Nextron.** The copy will lean on ABA Formal Opinion 512, the SRA's AI notice, Dynamics GP end of support, Harvey's seat minimum and case-study results. FR-43 mentions "approved company facts", but nothing defines them. A lawyer will spot a loose paraphrase at once. Opinion 512's informed-consent point concerns putting client information into *self-learning* generative AI tools, not "AI tools" in general; the brief's wording overstates it.
4. **Proof is the weakest link, and it isn't a launch blocker.** The brief says the case studies have no numbers, yet §9 doesn't list proof assets.

**Change:**
> **Glossary (add):** "**Claims Library:** the approved statements a Draft may make about anything other than the Lead: regulations, market facts, third-party products, Nextron's case studies, team and Offer. Each entry has the exact allowed wording, a source, a Market, an owner and a review date."

> **FR-21 (add):** "…and any statement about a regulation, a statistic, a third party or Nextron that doesn't match a Claims Library entry. A follow-up Step whose main point repeats an earlier Step of the same Sequence also fails."

> **FR-4 (replace the defaults):** "Default Step briefs, editable per Campaign:
> - **Step 1 (day 0, new thread):** ≤80 words. Opens on the Trigger (or Observation) in the prospect's own terms. One sentence on the likely consequence for a firm like theirs, one sentence on what Nextron does about it. CTA: one interest question that can be answered in a word (e.g., 'Is this on your radar?'). No meeting request, no price, no audit, no links except the signature.
> - **Step 2 (day 3, same thread):** ≤60 words. One proof item from the Claims Library that matches the angle: a case-study outcome, or how firms of their size handle the problem. The same interest CTA, reworded.
> - **Step 3 (day 7, same thread):** ≤60 words. A second angle from the Signal Playbook, or an offer that needs no meeting: a one-page resource, sent only if they reply (no link).
> - **Step 4 (day 14, same thread):** ≤50 words. A short, honest close with no guilt and no urgency. It asks who else owns the topic, or whether to check back next quarter.
>
> Each Step uses an angle or proof item not used earlier in the Sequence."

> **Glossary *Offer* / FR-3 (add):** "Each Offer defines the Step 1 interest question(s), the Step 3 resource (if any), the Reply ask (a 20-minute call) and the paid next step: the audit's scope, deliverables, duration, price and full credit toward a build. Price appears only in a Reply Draft that answers a direct price question."

> **§9 (add):** "8. **Proof assets:** at least one case study per Segment rewritten with numbers and entered in the Claims Library, plus each Segment's Step 3 resource (e.g., Law: 'questions to ask any AI vendor about client data'; Ops: 'what re-keying costs at your order volume'). *Owner: Furqaan; needed before Step 2 sends.*"

**Note on the law resource:** frame it as due-diligence questions for AI vendors, not a compliance checklist. A non-law firm telling lawyers what their ethics rules require reads as legal advice and as lecturing.

---

## TOP 3. Let the CEO answer hot replies from his phone within the hour, and make the Reply Draft book the call

**Where:** UJ-2; FR-42, FR-43; §5.4 (the dashboard is localhost only); §7 (*Not autonomous*); SM-8.

**Problem:**
1. **The hot reply waits for the PC.** In UJ-2, the alert reaches Furqaan within 5 minutes, but the reply waits until "the next time Mac opens the dashboard", which runs only on the PC (§5.4). The fastest handoff in the system stops at a desk. SM-8 accepts a median of 2 business hours.
2. **These replies are scarce.** The first quarter yields about 20 positive replies, and SM-2 needs at least half of them to become calls. Response speed is the cheapest lever on that ratio. Research on inbound leads shows the odds of qualifying a lead drop sharply after the first hour, and an "interested" reply to a cold email is no more patient.
3. **A bare booking link makes the prospect do the scheduling.** FR-43 puts "the booking link" in the reply, which asks a managing partner to do the admin. Answering the question and offering two specific times, with the link as a fallback, books more calls.

**Change:**
> **FR-42 (add):** "The alert includes the full Reply Draft (or 'draft pending'), so the Sender can send it himself without the dashboard."

> **FR-43 (add):** "The Sender may reply by hand from the Sending Inbox (e.g., Gmail on his phone; every Sending Inbox is in his name). The engine detects a message the Sender wrote in the thread within one polling cycle (FR-39). It logs that message as the response, marks any Reply Draft as superseded and keeps all Steps stopped."

> **FR-43 (replace the first sentence):** "For each *Interested* or *Question* Reply, the engine drafts a reply of ≤90 words. It answers the prospect's question in the first two sentences using only Claims Library facts. It offers two specific 20-minute times in the prospect's local time within the next three business days, taken from the Sender's configured weekly availability, and gives the booking link as an alternative. It makes one ask."

> **§7 (amend):** "Not autonomous: no outbound message of any kind goes out unless the Operator approved it or the Sender wrote and sent it himself."

> **SM-8 (replace):** "For Replies received during the prospect's business hours: median under 1 business hour from Reply to response; 90th percentile under 4 business hours."

> **UJ-2 (rewrite the ending):** "Furqaan reads the Telegram alert with the drafted reply, changes one line and sends it from Gmail on his phone 15 minutes later, offering two times that afternoon or the next morning. The engine logs the response."

**Setup dependency:** add to §9: "All Sending Inboxes are on Furqaan's phone. *Owner: Furqaan; needed before the first send.*"

---

## Other findings

### Signals, freshness and targeting

#### F-1. The hiring feeds in FR-7 barely reach this ICP (High)
**Where:** FR-7; PRD addendum §2 (*Job signals*).

**Problem:** Greenhouse, Lever and Ashby are used mostly by venture-backed tech companies. The ICP posts jobs elsewhere:
- small law firms: Indeed, LinkedIn, bar-association and legal job boards, or their own website;
- mid-size distributors and manufacturers: Indeed, ZipRecruiter, and payroll or HR suites (ADP, Paylocity, Paycom, UKG, BambooHR);
- UK firms: Indeed, Reed, Totaljobs and legal job boards.

Scraping Indeed or LinkedIn Jobs breaks their terms, so careers pages carry the load. Many careers pages only link out to an applicant-tracking system (ATS) that the crawler never follows. Hiring is the main automated Trigger in both Segments. If it runs dry, TOP 1 has little to work with.

**Change:**
> **FR-7 (replace):** "The engine finds job postings in three places: (a) the Lead's careers page; (b) any ATS board that the careers page links to or embeds, for a configured list of ATS hosts with public job pages (default: Greenhouse, Lever, Ashby, BambooHR, Workable, JazzHR, Recruitee, Breezy HR, Paylocity; the architecture step confirms each host's endpoint and terms); and (c) the Signal Inbox. Each posting stores its URL, title, posted date if shown, first-seen date and its live or closed status at each check. The dashboard shows hiring Triggers found per 100 Leads crawled, by Segment, so a dry source shows up in the first week."

> **FR-8 (add):** "The Signal Inbox accepts several items in one paste, including job-post URLs with their text, so the Operator can run a short daily search by hand (e.g., saved LinkedIn or Indeed searches for 'intake specialist' at law firms in target states) and feed in the results."

A person browsing by hand and pasting is within the platforms' terms. Only automated fetching isn't.

#### F-2. Known Leads are never re-checked, and there are no re-contact rules (High)
**Where:** FR-7, FR-9, FR-10, FR-19.

**Problem:** discovery is a single pass. A good-fit Lead with no Trigger today is never looked at again, and neither is a Lead whose Sequence ended without a reply. The week it posts the job or opens the warehouse is missed. The persona's core model, enrolment triggered by signals and promotion between tiers, depends on this loop. FR-9 prevents duplicate Leads, but nothing says when a Lead may be contacted again.

**Change:**
> **FR-7a (new) Watchlist:** "Every Lead that is Eligible, not in an active Sequence, and not Disqualified, Lost or suppressed is re-checked for Triggers on a schedule (default: the careers page and linked job boards weekly; the news, about and team pages monthly), within FR-10's crawl limits and FR-57's budget. A new Trigger re-scores the Lead and can move it to Tier A."

> **FR-19 (add) Re-contact:** "A Lead whose Sequence ended without a Reply may start a new Sequence only after 90 days, only on a Trigger first seen after that Sequence ended, and only with a different angle. For an Ops-Automation Tier A Lead, a second Contact in a different role may start 21 days after the first Contact's Sequence ends without a Reply. For Law-PrivateAI, the limit is one Contact per Lead per 90 days, because partners at a small firm compare notes. A Contact who replied *Not Interested* or *Unsubscribe* is never contacted again."

#### F-3. No speed-to-signal target, no fast lane and no freshness check before sending (High)
**Where:** FR-8, FR-19, FR-26, FR-31; §8.

**Problem:** nothing says how fast a Trigger becomes a sent email.
- A LinkedIn post pasted at 3 pm waits for the overnight run.
- FR-19 and FR-26 order by Lead Score, so a fresh Trigger can sit behind older high-fit Leads.
- FR-31 lets a Step 1 that cites a job posting go out up to 3 days after approval, even if the posting has closed.

The persona's 30-minute rule doesn't apply here (see *Persona advice the constraints override*). Days still matter: a LinkedIn post is a live conversation for about a week.

**Change:**
> **FR-8 (add):** "Signal Inbox items are processed when they are submitted, not in the overnight run. Their Drafts reach the Approval Queue within 15 minutes."

> **FR-26 (replace the sort rule):** "Queue order: Reply Drafts; then Step 1 Drafts for Tier A Leads, newest Trigger first; then other Step 1 Drafts; then follow-ups."

> **FR-31 (add):** "Before sending a Step 1 that cites a job posting, the engine re-checks the posting if it was last checked more than 24 hours earlier. If the posting has closed, the Draft returns to the queue flagged 'Trigger expired' for an edit."

> **§8 (add) SM-10 Trigger-to-send time:** "From Trigger first seen to Step 1 sent: median ≤2 business days for Tier A, ≤1 business day for Signal Inbox items."

#### F-4. Two of the brief's signals must not appear in copy (Medium)
**Where:** FR-5, FR-11; brief addendum C (*Reviews*; *Website: contact form only*).

**Problem:**
- **Review text** comes from Google Places, and FR-5 keeps only place IDs. A review excerpt therefore can't be stored as Evidence, and under FR-11 it can't be a Signal. Even if the terms allowed it, telling a managing partner "a client review says you never call back" is an insult, not an opener, and it invites spam complaints.
- **"Contact form only"** is a website critique that points toward selling an intake tool or chatbot, not private AI. As a stranger's first line, it reads like a web agency's pitch.

**Change:**
> **FR-11 (add):** "Google Places review content is not stored or used as a Signal in v1. A Signal Playbook row can be marked *score-only*: it can change Fit Score or Signal Score, but any Draft that mentions it fails the grounding check. Default score-only rows: 'contact form only' and 'no chat or booking'."

#### F-5. Segments say who's in, but not who's out (Medium)
**Where:** FR-1, FR-17.

**Problem:** in the persona's words, "if it does not exclude companies, it is not an ICP — it is a TAM slide". FR-1 has no disqualifiers. Accounts that look good on paper but never close reach drafting, and only the Operator's 20-second review catches them (like UJ-1's sole practitioner). Firm size matters too: a 5–9-lawyer firm rarely funds a $10–30k build, while the offer ladder fits firms of 10–50.

**Change:**
> **FR-1 (add):** "Each Segment has disqualifier rules (keyword, size and ownership tests), applied before scoring. A match marks the Lead Disqualified and shows the rule; rules marked 'review' flag the Lead instead.
> - Law defaults: fewer than 5 lawyers; legal aid or non-profit; an office of a firm with more than 50 lawyers in total; *review:* the firm has publicly announced an enterprise legal-AI platform.
> - Ops defaults: part of a group with more than 1,000 staff; a franchise location; fewer than 20 staff.
>
> Fit Score weights law firm size by band (default: 10–50 lawyers = 100%, 5–9 = 60%)."

### Sequence and copy

#### F-6. No subject-line rules, and fake reply prefixes aren't banned (Medium)
**Where:** FR-4, FR-21.

**Problem:** the subject line decides whether Step 1 is read, and the PRD says nothing about it. The persona advice includes first-email subjects like "re: the new data team", which are deceptive (see *Persona advice the constraints override*).

**Change:**
> **FR-4 (add):** "Step 1 subject: 2–5 words, in sentence case or lower case, about the prospect's world (the Trigger, their role or their practice), not about Nextron. No 'Re:' or 'Fwd:' prefix, no 'quick question', no emoji."

> **FR-21 (add to banned content):** "'Re:' or 'Fwd:' on a Step 1 subject; a mutual connection or referral that has no Referral record; any claim about a named competitor of the Lead."

#### F-7. One voice for two Markets (Medium)
**Where:** FR-2, FR-20.

**Problem:** UK solicitors are regulated by the SRA, not the ABA, and they talk about fee earners, matters and practices. UK operations buyers have Finance Directors, not Controllers. A US-flavored email to a UK LLP ("your attorneys", "ABA guidance") marks itself as mass mail.

**Change:**
> **FR-2 (add):** "Each Market carries copy rules that drafting follows and the grounding check enforces: spelling (US or UK English); role and industry vocabulary (UK defaults: solicitors, fee earners, Finance Director); date and currency formats; and which regulatory Claims Library entries may be cited (US: ABA and state bars; UK: SRA and ICO). A Draft that cites another Market's regulator fails."

#### F-8. Furqaan's voice is not defined (Low)
**Where:** FR-20 ("in Furqaan's voice").

**Change:**
> **FR-20 (add):** "The Sender has a Voice Profile: 5–10 emails he actually wrote, his sign-off, phrases he uses and phrases he never uses, and a true one-line description of himself (e.g., 'I run a 17-person AI engineering team in San Jose'). Drafts are generated against it, and the Voice Profile is versioned with the prompts (§5.6)."

Honest, small-team framing from a founder beats "we are a leading provider", and both persona files agree.

#### F-9. Follow-ups written on day 0 can go stale (Low)
**Where:** FR-20, FR-26.

**Change:**
> **FR-26 (add):** "When a follow-up enters the queue, the engine regenerates it if the Lead's Evidence has changed since drafting (a new Trigger or a closed posting), or if a Claims Library entry it uses has been edited. Otherwise it uses the stored Draft."

#### F-10. Variant tests can't reach a result at this volume (Medium)
**Where:** FR-3; FR-54; brief addendum D ("two variants per segment each month").

**Problem:** about 22 new Contacts a day over four Campaigns is about 5 per Campaign and 2–3 per Variant. Over the first quarter that is about 160 Contacts per Campaign-Variant, and two or three positive replies each. Monthly "winners" will be noise. The persona's rule also applies: test one variable at a time.

**Change:**
> **FR-3 (add):** "A Variant pair is defined at Segment level, pooled across Markets unless the Operator chooses otherwise. It states a hypothesis and changes exactly one element: the angle, the CTA, the Step 2 proof or the subject. It runs until each Variant has at least 150 contacted Contacts."

> **FR-54 (add):** "Each Variant comparison shows contacted, reply and positive-reply counts. It is labeled 'not enough data' until the minimum is met. Reply rate is the main comparison until each Variant has 10 positive replies."

Test the angle for each Trigger type first, because it has the largest effect. Without open tracking, subject-line tests are the least useful.

### Replies and objections

#### F-11. The Reply Classes miss the replies that create calls and the ones that need care (High)
**Where:** Glossary *Reply Class* and *Hot Lead*; FR-40.

**Problem:** four classes are missing:
- **Objection**, with a type.
- **Wrong Person**, when no name is given.
- **Left Company**: an auto-reply that often names a successor, which is a free warm lead.
- **Source Question**: "How did you get my email?" Today this lands in *Question*, makes a Hot Lead and gets a reply with a booking link, which is the worst possible answer.

*Not Now* also doesn't capture when to come back.

**Change:**
> **Glossary *Reply Class* (replace):** "*Interested, Question, Objection, Referral, Wrong Person, Left Company, Not Now, Not Interested, Unsubscribe, Source Question, Out of Office, Bounce, Other*."

> **FR-40 (add):** "*Objection* carries a type from the Objection Playbook (F-12). *Not Now* extracts any stated re-contact date. *Left Company* extracts any named successor and their address. A *Source Question* gets a Reply Draft built from the Contact's Provenance Record (FR-51) plus the opt-out offer, with no booking link and no pitch."

> **Glossary *Hot Lead* (replace):** "a Lead whose latest Reply Class is *Interested*, *Question* or *Referral*, or an *Objection* that the Operator marks as engaged."

#### F-12. Objections get no Reply Drafts; only Hot Leads do (High)
**Where:** FR-43.

**Problem:** objections are the most common reply in cold B2B outreach that isn't a flat no, and an objection means the prospect is engaged. The PRD drafts nothing for them, so they either wait for a busy CEO or get dropped. The sales-outreach framework's method fits: acknowledge, answer briefly, ask one question, propose one step.

**Change:**
> **FR-43a (new) Objection Playbook:** "Each Segment has a list of objection types. Each type has an approved answer pattern built from Claims Library facts and one follow-up question. Reply Drafts are generated for *Objection*, *Wrong Person*, *Left Company* and *Not Now* Replies as well as for Hot Leads. Each one acknowledges the point in one line, answers in at most two sentences, asks one question and offers one next step, in ≤90 words. It never argues with the prospect or criticizes a named product.
> - Law defaults: 'we use Copilot / ChatGPT Enterprise / Clio Duo / CoCounsel'; 'AI is banned here'; 'where does client data go?' (UK: 'is it hosted in the UK?'); 'who else have you done this for?'; price; 'we're too small'; 'our IT provider handles this'; 'send me something'.
> - Ops defaults: 'we're replacing our ERP'; 'our ERP partner does this'; 'we tried automation and it broke'; 'will AI make mistakes on orders?'; price; 'not a priority'; 'send me something'."

> **FR-54 (add):** "Objection types, and how many of each led to a booked call."

For "send me something", answer in two lines and ask one qualifying question before sending anything, as the sales-outreach framework advises.

#### F-13. Referrals aren't turned into warm introductions (High)
**Where:** FR-40.

**Problem:** FR-40 extracts the referred person, then stops. "Talk to Dana, our ops manager" is the warmest opener the engine will ever get; the persona benchmark is a 30–50% reply rate for referral-based outreach. That only holds if the next email names the referrer, reflects what they said accurately and goes out fast.

**Change:**
> **FR-40a (new):** "For a *Referral*, or a *Left Company* reply that names a successor, the engine creates the referred Contact at the same Lead (FR-14 to FR-18 apply). It drafts (a) a short thank-you to the referrer and (b) a Step 1 to the referred person that names the referrer and accurately reflects what they said, in a new thread from the same Sending Inbox. Both Drafts go to the top of the queue. The referred Contact gets a two-Step Referral Sequence."

#### F-14. Not Now and Not Interested need opposite handling (Medium)
**Where:** FR-37, FR-40, FR-41.

**Problem:** both replies just stop the Sequence. "Try me in Q2" is a scheduled opportunity. "Not interested" is a no, and in the UK it can reasonably be read as an objection to direct marketing.

**Change:**
> **FR-37 (add):**
> - "*Not Now:* the Reply Draft thanks them and says when Furqaan will check back. One re-contact Draft is scheduled for the stated date (default 90 days). It enters the Approval Queue on that date and re-runs Eligibility and the Suppression check.
> - *Not Interested:* an optional one-line courteous reply, with no question and no pitch. The Contact's address goes on the Suppression List."

#### F-15. A colleague's reply doesn't stop the Sequence (Medium)
**Where:** FR-37, first bullet.

**Problem:** at a 12-lawyer firm, the managing partner forwards the email and a partner or the firm administrator replies. The stop rules key on "the Contact", so the original recipient keeps getting follow-ups after the firm has already answered.

**Change:**
> **FR-37 (replace the first bullet):** "any Reply, other than *Out of Office*, from the Contact or from any address at the Lead's domain. A Reply from someone else at the Lead is linked to the Lead, creates a Contact if that person is new, and is classified and drafted like any other Reply."

### Channels

#### F-16. LinkedIn Tasks: the wrong account, unrealistic notes and no sequencing (High)
**Where:** UJ-3; FR-24, FR-48; §5.1 *Platform terms*.

**Problem:**
1. **Whose account.** In UJ-3, Mac sends the connection note.
   - From Mac's own profile, it is a stranger following up on someone else's email.
   - From Furqaan's profile, it is account sharing, which LinkedIn's User Agreement forbids. That puts the CEO's profile, which the brief calls a sales asset, at the same risk FR-49 exists to avoid.
2. **Note limits.** LinkedIn currently caps personalized invitation notes on free accounts: a small monthly allowance, at 200 characters. FR-24's 300-character note on 20 invites a day assumes a paid plan. Premium costs about $40–60 a month, a large share of the budget headroom. Confirm Furqaan's account type.
3. **Order and content.** Nothing says when the LinkedIn touch happens relative to email, or what it may say. The persona's rule: connect with no pitch, and make the first message add something new. For a LinkedIn post found through the Signal Inbox, the best first touch is a substantive comment on that post, then a connection request, within a day or two.

**Change:**
> **FR-48 (replace):** "The Sender carries out LinkedIn Tasks from his own account; nobody else operates it. Default: 10 Tasks a day, in this order: Signal Inbox Leads, then Hot Leads, then Tier A Leads 1–2 business days after their Step 1 is sent. Each Task gives:
> - the profile link;
> - a connection note of ≤200 characters with no pitch and no link, or 'send without a note' once the month's note allowance is used up;
> - a first message for after acceptance that adds something the emails didn't (≤300 characters);
> - for Signal Inbox posts, a drafted comment on the post.
>
> The Operator records accepted connections, and the engine tracks the note allowance."

> **FR-24 (change):** "connection note (≤200 characters, no pitch)".

#### F-17. Every Sending Inbox must present as Furqaan (Low)
**Where:** FR-32; brief addendum A, step 3 (`team@`).

**Problem:** the brief suggests `team@` as one of the three inboxes. A CEO-signed email from `team@` reads as a mass send and breaks the one-to-one conversation the copy depends on.

**Change:**
> **FR-32 (add):** "Every Sending Inbox uses the Sender's name as its From name, and its address is a personal form of that name (e.g., furqaan@, f.ahmed@, furqaan.ahmed@). Role addresses (team@, info@, sales@) are refused."

### Call preparation and pipeline

#### F-18. Nothing prepares the CEO for the discovery call (High)
**Where:** FR-47; UJ-2; FR-45.

**Problem:**
- FR-47's call sheet supports warm-channel follow-up (a phone number, talking points and the Offer). Nothing prepares Furqaan for the booked discovery call, which is the moment the engine exists to create, and nothing defines a good outcome. Founder-led first calls usually fail by pitching too early and ending without a dated next step.
- Bookings aren't detected either. Mac moves Leads to *Call Booked* by hand (UJ-2), so both the call preparation and SM-2 depend on someone remembering.

**Change:**
> **FR-47a (new) Call Brief:** "When a Lead enters *Call Booked*, the engine generates a one-page Call Brief. It is shown on the Lead page and emailed to the Sender's main inbox the evening before the call. It contains:
> 1. the Lead in five lines (what they do, size, locations, systems mentioned), with Evidence links;
> 2. the Contact's role;
> 3. the thread: the Trigger cited, every message sent, and their reply in full;
> 4. two or three pain hypotheses, labeled as hypotheses;
> 5. six to eight discovery questions for this Segment and Trigger, covering their situation, the problem, its impact (hours, money or risk), current tools, who decides, timing and budget range;
> 6. likely objections, with Playbook answers;
> 7. the Claims Library proof items to mention;
> 8. the audit to propose (scope, deliverables, price, duration, credit toward a build) and the goal: agree the audit and a start date, or a specific dated next step;
> 9. the disqualifiers to check.
>
> It uses only Evidence and the Claims Library."

> **FR-45 (add):** "The engine detects new bookings, through the Cal.com API or booking notification emails (the architecture step decides). It moves the Lead to *Call Booked* and records the call time. The booking form asks 'What would you like to cover?', and the answer feeds the Call Brief."

#### F-19. Call outcomes aren't captured, so the Offer can't be validated (High)
**Where:** Glossary *Pipeline Stage*; FR-43, FR-45; §10 (last bullet); SM-1, SM-2.

**Problem:**
- §10 says the Offer will be "validated on the first 10 calls", but no requirement records what happens on a call.
- The stages jump from *Call Booked* to *Audit Proposed*, with no stage for a call that was held, a no-show or nurture.
- Meetings from cold outreach often see 20–30% no-shows, yet nothing drafts a confirmation or a rebooking email.

**Change:**
> **Glossary *Pipeline Stage* (replace):** "*New → Queued → Contacted → Replied → Interested → Call Booked → Call Held → Audit Proposed → Audit Won → Build Proposed → Won*; *No-show* (back to *Call Booked* when rebooked); *Nurture* (a dated re-contact); *Lost* or *Disqualified* at any point."

> **FR-45a (new) Call Outcome:** "After a call's scheduled time, the dashboard asks the Operator to log: held or no-show; qualified (yes or no, and why); pains (tags); who decides; budget range; objections raised (Playbook types); whether the audit was proposed, and at what price; the next step and its date. The Lead's stage updates from this record."

> **FR-43 (add) Call Drafts:** "A confirmation from the Sender on the business day before the call (≤50 words, with a one-line agenda). On a no-show, a rebooking Draft the same day with two new times, one more after 3 business days, then stop."

> **§8 (add) SM-11 Offer validation:** "After 10 held calls, report how many audits were proposed and accepted, and the top three objections. If fewer than 2 audits are accepted, review the Offer before the next Variant cycle."

### Metrics

#### F-20. The funnel can't show what's working (High)
**Where:** FR-54.

**Problem:** FR-54 breaks results down by Campaign, Segment, Market, Variant and time. What actually explains reply rates is the Tier, the Signal type, the Step, the role and the inbox. Without those, nobody can answer:
- Do hiring Triggers beat news Triggers?
- Does Step 3 ever get a reply?
- Is one inbox landing in spam?

There are also no conversion rates between stages and no timing measures, even though SM-8 is a timing target.

**Change:**
> **FR-54 (add):** "Dimensions: Lead Tier; Personalization Level; primary Signal type; Signal source (automated or Signal Inbox); Contact role; the Step after which the first Reply came; Sending Inbox; LinkedIn touch (yes or no). Measures: negative-reply rate; conversion between each pair of stages from positive Reply to Won; median Trigger-to-send time (SM-10); median Hot Lead response time (SM-8); median time from Reply to booking; no-show rate; LinkedIn Tasks sent, acceptance rate and Replies from LinkedIn; open pipeline value by stage."

#### F-21. The reply rate counts opt-outs (Medium)
**Where:** SM-4; FR-23; SM-C3.

**Problem:** FR-23's opt-out is "reply 'no'", which is good: it is human and easy, and replies help sender reputation. But every "no" counts as a reply in SM-4, so the raw reply rate goes up when the copy gets worse. SM-C3 names this risk, but nothing measures it.

**Change:**
> **SM-4 (replace the definition):** "All human Replies except *Out of Office*, *Bounce* and *Unsubscribe*, divided by Contacts contacted."

> **§8 (add) SM-12 Negative-reply rate:** "(*Not Interested* + *Unsubscribe*) ÷ Contacts contacted, per Variant and Signal type. Investigate any Variant whose rate is more than twice its Campaign's average."

#### F-22. Targets aren't split by Tier, and there's no early-warning rule (Medium)
**Where:** §8: SM-2, SM-3, SM-4.

**Problem:** a 1.5% positive reply rate and a 4% total reply rate are reasonable blended floors, roughly the 2026 averages the brief cites. But they can't show whether the signal work pays off. Nothing stops the engine if the first few hundred Contacts show that the copy or the targeting is wrong, and the first quarter's ~1,300 Contacts are the whole learning budget.

**Change:**
> **§8 (add):**
> - "**SM-3a / SM-3b:** positive reply rate ≥3% for Tier A and ≥1.5% for Tier B. [ASSUMPTION: recalibrate after 300 contacted per Tier.]
> - **Early warning:** after 300 Contacts in a Segment, if the positive reply rate is below 1%, new Step 1s for that Segment pause until the Operator records a change to targeting, angle or Offer.
> - **SM-2 (add):** report calls held and the share that were qualified, not only calls booked."

#### F-23. There's no inbox-placement check, and most buyers use Microsoft 365 (Medium)
**Where:** FR-38, FR-54, §5.5.

**Problem:** with no open tracking (which is correct), spam-foldering only shows up as fewer replies, weeks later, and it looks exactly like bad copy. Law firms and mid-size distributors and manufacturers mostly run Microsoft 365, which is strict with new domains. Mail from Google Workspace into Outlook is where cold programs fail without anyone noticing.

**Change:**
> **FR-38 (add):** "Each week the engine sends a placement test from each Sending Inbox to a seed list the Operator owns (default: at least one Gmail, one Outlook.com and one Microsoft 365 mailbox; a Microsoft 365 mailbox costs a few dollars a month). The test uses a recent approved Step 1 body with the Contact details replaced. The Operator records inbox or spam for each seed, or the engine reads connected seed mailboxes. An inbox that lands in spam at a Microsoft seed two weeks running is flagged on the Health panel. FR-54 shows the reply rate per Sending Inbox by week."

---

## Persona advice the constraints override

The persona files are general B2B playbooks. Where their advice conflicts with this PRD's legal, platform, deliverability or budget constraints, the constraints win.

| Persona advice (source) | Conflicts with | Use instead |
|---|---|---|
| Route signals within 30 minutes; a signal is stale after 24 hours (Outbound Strategist, *Speed-to-Signal*) | Every email is human-approved (FR-26 to FR-30); Send Windows (FR-34); no paid intent data | SM-10: ≤1 business day for Signal Inbox items, ≤2 for Tier A (F-3). The 30-minute rule is for inbound and intent-data signals, which Nextron doesn't have. |
| Tier 1 intent signals: review-site visits, pricing-page views, competitor-comparison searches (Outbound Strategist) | The $100-a-month cap (intent data and website-visitor identification cost far more); identifying UK website visitors needs cookie consent under PECR and UK GDPR | Not in v1. |
| Multi-thread 3–5 contacts per Tier 1 account (Outbound Strategist) | FR-19 (one active Contact per Lead); the 30-a-day inbox cap; complaint risk at 5–50-person firms | A second Contact in sequence, for Ops Tier A only (F-2). |
| 8–12 touches over 3–4 weeks, including phone calls and voicemail drops on days 8 and 24 and a Loom video on day 17 (Outbound Strategist; Sales Outreach's 7-touch cadence) | The Daily Cap (a fifth email Step cuts new starts by about a fifth); brief addendum B, "Phone: warm leads only"; ringless voicemail counts as a call under the TCPA (FCC, 2022) and needs consent for mobile numbers; unsolicited UK sales calls need TPS/CTPS screening; video links in cold follow-ups from a new domain add filtering risk | Four email Steps plus hand-sent LinkedIn. Phone and Loom only after a reply (FR-47). |
| A subject like "re: the new data team" (Outbound Strategist) | CAN-SPAM bans subject lines likely to mislead about the content; FR-21 bans fake familiarity | F-6: no "Re:" or "Fwd:" on Step 1. |
| Subjects like "[Mutual connection] suggested I reach out" and "[Their competitor] is doing this — are you?" (Sales Outreach) | Grounding (FR-20, FR-21) | Only with a Referral record (F-13); no competitor claims without Evidence. |
| Social proof like "[Similar company] cut [metric] by [number]" (both files) | FR-21 bans made-up metrics; the case studies have no numbers yet | Claims Library only (TOP 2), and only after the case studies are rewritten. |
| After "not interested", ask whether timing is the issue and set a re-engagement reminder (Sales Outreach, *Handle Responses*) | UK GDPR Art. 21 (an absolute right to object to direct marketing); the intent of FR-41 | No follow-up question; suppress the address (F-14). A Lead lost after a held call is a different case: a later check-in is fine if they haven't objected and the Suppression List is checked. |
| Automated engagement scoring to promote accounts between tiers (Outbound Strategist, Tier 3) | §5.5: no open or link tracking | Engagement means Replies and LinkedIn acceptances only. |
| Respond to positive replies within 1 hour (Sales Outreach) | Achievable only if the Sender can reply himself; approval on a localhost dashboard can't meet it | TOP 3. |
| A signature with a phone number and a LinkedIn URL (Sales Outreach) | FR-4 allows signature links, but each extra link in cold email from a new domain adds filtering risk | One link (the website) in the Step 1 signature. |
| Cold emails under 150 words (Sales Outreach) | The PRD is stricter (80 words) | Keep 80. |
| Direct mail and event plays for Tier 1 accounts (Outbound Strategist) | Budget | Not in v1. |

## Notes for other reviewers (outside sales scope)

- **UK GDPR Art. 14(3)(b).** When contact data wasn't collected from the person, privacy information is due at the latest at the first communication. UK Step 1s probably need a short privacy line or a privacy-notice link in the FR-23 footer. FR-4 already allows a signature link. Route this to the compliance review.
- **ABA Formal Opinion 512.** The brief's one-line summary is broader than the opinion. Correct it in the Claims Library before any law-firm copy uses it (TOP 2).

---
title: "Reconcile: Brief to PRD, Nextron Outreach Engine"
created: 2026-11-14
inputs:
  - _bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/brief.md
  - _bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/addendum.md
  - _bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/.memlog.md
  - _bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/prd.md
  - _bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/addendum.md
  - _bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/.memlog.md (context only)
---

# Brief to PRD Reconciliation: Nextron Outreach Engine

## Summary

**Method.** I compared every commitment, constraint, number and piece of qualitative intent in the brief, the brief addendum and the brief decision log against `prd.md` and the PRD addendum. I also read the PRD's own `.memlog.md`, so each finding can say whether a change was **logged** (a recorded PRD decision or assumption) or **silent** (no record). Line numbers ("L") refer to the files as they stood on 2026-11-14. The abbreviation "brief-add" means the brief addendum and "prd-add" means the PRD addendum.

**Counts.** 16 dropped, weakened or contradicted items (§1), 9 scope additions (§2) and 11 numeric inconsistencies (§3). §4 lists what was checked and found consistent.

**Top gaps**
1. **The volume math doesn't hold (N-01, N-02).** Unverified (*Likely*) addresses are capped at 30% of sends, but only 150 finder and verifier credits arrive each month. That limits new Contacts to about 10 a day, against 20–25 in the PRD and 500–600 prospects a month in the brief. The Warm-up Ramp also starts already-warmed inboxes again from 5 a day, so the first 6 weeks see about 42% fewer cold sends.
2. **One-click unsubscribe is out of scope (R-01).** It is a v1 compliance item in the brief and a hard rule in the brief addendum, but the PRD removes it on the strength of an assumption.
3. **The hand-off to Furqaan is weaker (R-03).** His alert carries no drafted reply and no booking link, the dashboard works only on localhost, and the Reply Draft waits until Mac is at the PC.
4. **The approval model is reversed (R-02).** The brief says approve, then auto-send. The PRD has every follow-up approved when it falls due. This is logged as Mac's choice but was never carried back to the brief, and its effects (about 90 approvals a day, follow-ups stalling when no one is at the PC) are not reconciled.
5. **Prerequisites, risks and voice are missing (R-04, R-05, R-07, R-08).** The prerequisites list and the risks section are gone. Inbox purchase now waits on the warm-up decision. "Furqaan's voice" and the store of approved Nextron facts (case studies, regulatory notes) are never defined.

**Severity key.** *High*: contradicts a brief commitment or hard rule, or materially threatens the primary goal (first paid engagement within 90 days). *Medium*: drops or weakens something that affects outcomes. *Low*: wording, definitions or minor drift.

---

## 1. Dropped, weakened or contradicted

### R-01 · High · Contradicted: one-click unsubscribe moved out of v1
- **Source:** brief.md §Scope "In v1" L97 ("compliance basics: one-click unsubscribe, a postal address, a suppression list…"); brief-add §B L28 (hard rule for cold email: "one-click unsubscribe").
- **PRD:** FR-41 L311 (`[ASSUMPTION]` mailto `List-Unsubscribe` only); §6.2 L434 (a public one-click endpoint is out of scope); §10 L485. PRD .memlog L15 records this as an *assumption*, not a decision.
- **Gap:** Of the brief's five compliance basics, this is the only one the PRD drops. The brief gives no volume threshold, yet the PRD justifies the drop with "below 5,000 a day". The footer line ("Reply 'no'") is a reply-to-unsubscribe, not a one-click unsubscribe. The brief does carry a real tension here, because it also puts cloud hosting out of v1. The PRD resolves that tension on its own without recording a decision.
- **Fix:** Pick one and record it.
  - (a) Restore the requirement: a minimal hosted unsubscribe endpoint (a free-tier form or worker that the engine polls), explicitly carved out of "no cloud hosting", plus an RFC 8058 `List-Unsubscribe-Post` header.
  - (b) Get Mac's sign-off that mailto plus reply detection replaces "one-click" for v1, and log it as a change in the brief memlog. Then update brief §Scope L97 and brief-add §B L28 to match.

  Either way, remove the `[ASSUMPTION]` tag.

### R-02 · High · Contradicted (logged in the PRD, not carried back): approval granularity
- **Source:** brief.md L18 ("The operator approves each batch. The engine then sends emails and follow-ups"); L55–57 (draft the four-email sequence, then approve, then send; "Follow-ups stop when a prospect replies or unsubscribes"); L94 ("cold email, fully automated after approval"); L80 (approval takes under 30 minutes a day); brief .memlog L6 ("approve-then-auto-send").
- **PRD:** §4.7 L233 ("Every email, first emails and follow-ups alike, needs explicit Operator approval… (Mac's decision)"); FR-26 L236; FR-29 L255; FR-30 L257–258; FR-31 L261; UJ-1 L53 (24 first emails plus 61 follow-ups); OQ7 L477. PRD .memlog L12 records this as Mac's choice.
- **Gap:** The decision is real, but the brief still says "fully automated after approval", so the two documents disagree. The effects are also unreconciled:
  - Daily approvals rise from about 20–25 sequences to about 90 items. That is roughly 20 seconds per item, including the Evidence check, to meet the 30-minute target, which raises the risk of rubber-stamping (the PRD itself names this as SM-C4).
  - Follow-ups go out only after someone approves them at the PC. The brief never required daily operator presence for follow-ups, and a missed day pushes back the day 0/3/7/14 cadence.
- **Fix:**
  - Log the change in the brief memlog and amend brief L18 and L94.
  - In the PRD, state what happens to Sequence timing when follow-ups wait for approval.
  - Consider making approval of the full Sequence at Step 1 the default, so the card shows all four Steps and follow-ups send unless edited. Alternatively, make FR-30 batch approval the default for follow-ups now, rather than waiting for OQ7.
  - §7's rule that "no outbound message goes out without Operator approval" still holds under Sequence-level approval.

### R-03 · High · Weakened: hand-off of hot leads to Furqaan
- **Source:** brief.md L18 ("hands interested prospects to Furqaan for a discovery call"); L58 ("Interested leads go to Furqaan with a booking link, a drafted reply and, optionally, a drafted WhatsApp, Instagram or LinkedIn follow-up"); L36.
- **PRD:** FR-42 L315 (the alert contains the Lead and Contact, the Reply text, the Signals and a dashboard link); FR-43 L318–320 (the Reply Draft goes to the Approval Queue); §5.4 L413 (localhost only); UJ-2 L54 ("The next time Mac opens the dashboard…"); SM-8 L460 (median under 2 business hours); JTBD L43 (Furqaan wants "a drafted reply"). PRD .memlog L13: "Furqaan works from alerts plus email".
- **Gap:** Furqaan's alert has no drafted reply and no booking link, and its dashboard link works only on the local PC. The Reply Draft sits in the PC queue until Mac next opens the dashboard. The brief's hand-off ("to Furqaan, with a drafted reply") has become a notification. Response time now depends on Mac being at the PC, which puts SM-8 and the PRD's own JTBD ("while they're warm") at risk. The memlog intent that Furqaan "works from alerts plus email" can't be met with the alert content FR-42 specifies.
- **Fix:**
  - Put the Reply Draft text and the booking link in the email and Telegram alerts.
  - Let Furqaan act without the PC, for example by replying himself from the Sending Inbox thread (`furqaan@` on the lookalike domain). The engine then detects the human-sent reply and marks the Draft as handled.
  - Drop or qualify the "dashboard link" for use away from the PC.
  - Add an acceptance criterion that ties FR-42 and FR-43 to SM-8.

### R-04 · High · Dropped: prerequisites outside the engine, and inbox-purchase timing
- **Source:** brief.md §Prerequisites L109–114: domain and 2–3 inboxes warming for 2–4 weeks; booking link to replace the website's mailto link; postal address; 1–2 case studies rewritten with numbers; updated LinkedIn profile for Furqaan and company page for Nextron. Also brief.md L118 ("Every week of delay pushes the first send back a week"); brief-add §A "do first"; brief .memlog L18 (buy now so warm-up overlaps the build).
- **PRD:** no prerequisites section. §0 L18 says the setup checklist isn't repeated. §9 L471–476 covers only the postal address, booking link, Telegram, warm-up method and the LIA. OQ1 L471 says "decide before buying the inboxes".
- **Gap:** Three of the brief's five prerequisites have no PRD home: case studies with numbers, Furqaan's LinkedIn profile and the company page, and replacing the website's mailto link with the booking link. Domain and inbox purchase isn't tracked as a gate. OQ1 makes the purchase wait on the warm-up-method decision, the reverse of "do first" and the memlog's "buy now". The brief says the engine "can't convert prospects without these".
- **Fix:** Add a table of external prerequisites that gate the first send, with columns for item, owner, needed-by and status. Include the brief's five items plus the PRD's additions (an LIA per UK Campaign, the Telegram chat, the Cal.com event type). Reword OQ1: buy the domain and inboxes now, because the warm-up method doesn't block the purchase and manual warm-up can start the same day.

### R-05 · Medium · Dropped: risks and their mitigations
- **Source:** brief.md §Risks L116–122: warm-up timing; credibility ("lead with the regulatory angle and offer the audit as a low-risk first step"); PC uptime; email-verification limits that put the under-2% bounce target at risk; AI accuracy.
- **PRD:** no risks section. Some risks are covered implicitly: uptime by FR-35/36, verification by FR-15/16, AI accuracy by FR-20/21. The credibility mitigation appears only as a Regenerate example (FR-25 L230: "lead with the SRA notice").
- **Gap:** Warm-up timing and credibility have no PRD home. Leading with the regulatory angle is not a default for Law-PrivateAI drafting. The verification risk is acknowledged only in prd-add L42, and the PRD actually makes it worse (see N-01).
- **Fix:** Add a short §Risks that lists each brief risk, the FRs that mitigate it and the residual risk. Make the regulatory angle the default in the Law-PrivateAI Step 1 writing brief (FR-4), and make "the audit as a low-risk next step" a rule for Reply Drafts (FR-43).

### R-06 · Medium · Dropped: the brief's scope rule and time-to-first-client urgency; no build priority
- **Source:** brief.md L20 ("Every scope decision below is judged by whether it shortens the path to the first signed engagement"); brief .memlog L10 ("time-to-first-client is critical"). PRD .memlog L7 (business-critical) and L8 (fast path) also note it.
- **PRD:** §6.1 L428 ("All of §4"); none of FR-1 to FR-58 carries a priority or phase tag.
- **Gap:** All 58 FRs count equally as MVP, and the brief's scope rule has disappeared. Nothing tells architecture and epics which FRs must exist for the first cold email and which can follow. The first-send set is discovery → enrichment → Eligibility → drafting → approval → sending → stop rules → unsubscribe and suppression → reply detection and alerts. Items that can follow include funnel analytics, DSAR export, the retention job, prompt versioning, the Segment editor and approval metrics. Warm-up already runs on a 2–4-week clock, so building everything in sequence delays the first send.
- **Fix:** Restate the brief's scope rule in §1 or §6. Tag each FR "First send", "First month" or "Later v1". Define a walking-skeleton release that can send the first approved emails as soon as the inboxes are warm.

### R-07 · Medium · Dropped (qualitative): voice, tone and the "feel" of messages
- **Source:**
  - brief.md L18: "short outreach that cites that evidence… says something specific to each one".
  - brief.md L25: the senders who do well use tight segments, under 80 words, one CTA and a specific reason.
  - brief.md L67: "a low-effort reply ('worth a 20-minute look?') tied to the specific signal observed".
  - brief-add §D L68: "one observed signal, one question-style call to action".
  - brief-add §D L69: "Each follow-up adds new value (a short insight, a relevant case study, a regulatory note), never 'just bumping this.'"
  - brief-add §D L70: plain text, from Furqaan.
  - brief-add §A.3: real-looking inbox names, a photo and a signature (a human feel).
- **PRD:** §4.6 L208 ("The AI writes Drafts in Furqaan's voice"), with no definition anywhere. FR-4 L114–115 covers the word limit and one CTA, but not "one signal" or "question-style". FR-21 L216 lists banned content (fake familiarity, made-up metrics, false urgency) but has no rule against bumps or follow-ups with no new value.
- **Gap:** "Furqaan's voice" has no spec, no examples and no owner. Also missing are the low-pressure, question-style ask, the rule of one signal per Step 1, and the rule that each follow-up brings new value. The brief cites these as the reason top senders beat the 2.1% median reply rate. A check that counts only words and links will pass copy that is bland or pushy.
- **Fix:** Add a Voice and Message Rules section or FR:
  - peer-to-peer, plain and specific, with no hype or flattery;
  - Step 1 cites exactly one observed Signal and ends with one low-effort question CTA;
  - each follow-up adds one new item of value and never says "just bumping" or "following up";
  - plain text;
  - 3–5 example emails per Segment, approved by Furqaan, used as few-shot anchors.

  Add "more than one Signal in Step 1" and "a follow-up with no new value" to the FR-21 checks. Owner: Furqaan approves the voice guide.

### R-08 · Medium · Dropped: Nextron's proof and regulatory facts; grounding covers only claims about the Lead
- **Source:** brief.md L45 (proof by Segment: the Legal AI case study for law, Wish Wheels for operations); L43 (why-now facts: ABA 512, the SRA notice of August 2026, Harvey's ~20-seat minimum, Dynamics GP support ending in 2029, the 85% and 70% statistics); L119 (the case studies have no metrics); L122 ("Every message uses only stored evidence"); brief-add §D L69 (follow-ups use case studies and regulatory notes).
- **PRD:** FR-20 L212 ("Any factual claim about the Lead must map to stored Evidence"); FR-21 L216 (checks claims about the Lead only); FR-43 L318 (uses "approved company facts", which is undefined and not in the Glossary). No FR stores or curates these facts.
- **Gap:** Follow-ups and Reply Drafts are meant to cite Nextron's proof and regulatory facts, but the PRD gives them nowhere to live. The AI will either invent them or draw on model memory. A misquoted ABA 512 or SRA notice sent to a law partner is exactly the credibility and AI-accuracy risk the brief warns about. The mapping of proof to Segment (Legal AI case study for law, Wish Wheels for operations) is also lost.
- **Fix:** Add an FR for an Approved Facts library: proof points by Segment, case-study summaries (with numbers once they are rewritten, per R-04), regulatory notes with citation URLs, and the Offer terms. The Operator can edit it. Extend the FR-21 grounding check to claims about Nextron and about regulation. Add "Approved Fact" to the Glossary.

### R-09 · Medium · Dropped or thin: the review signal, the news signal and the SRA check
- **Source:** brief.md L44 (law signal: "reviews about unreturned calls"); L53 ("check each company's website, job posts and reviews"); brief-add §C L55 (reviews come from Google Places review fields, Enterprise SKU, within the free limit); L51 (SRA register as a UK law-firm check, API availability unconfirmed); L63 (news: a new facility, expansion or acquisition).
- **PRD:** FR-5 L121–122 (records the place ID, name and website, and keeps only the place ID long-term); FR-10 L143 (the crawl list has no news or press page); FR-7; FR-8. No FR covers reviews, and the SRA isn't mentioned.
- **Gap:** One of the three observable law signals has no source in the PRD. The PRD's own assumption that only the place ID is stored would also block keeping a review excerpt as Evidence, which FR-11 requires as a verbatim excerpt. The operations signal "new facility or acquisition" depends on the Operator pasting news by hand (FR-8), because the crawl skips news and press pages. The SRA register isn't mentioned, not even as an open question.
- **Fix:** Decide explicitly. Either add review fetching and resolve how a review can count as Evidence under the Places terms (OQ5), or drop the review signal from the Segment with a recorded reason. Add "news, press and blog" to the FR-10 crawl list. Add an open question on SRA register availability.

### R-10 · Medium · Weakened: WhatsApp opt-in and the Instagram 24-hour rule
- **Source:** brief.md L96 ("WhatsApp and Instagram only for leads who have already replied"); brief-add §B L30 (Instagram: a business may message only after the user engages, within a 24-hour window); L31 (WhatsApp: "Only numbers the prospect shared **and opted in**; approved templates outside the 24-hour window").
- **PRD:** FR-24 L227 ("used only when the prospect has engaged on that channel **or shared a number**"); FR-47 L335; §5.1 L399.
- **Gap:** Sharing a number, for example in an email signature, is not opting in to WhatsApp. The brief requires both. The Instagram 24-hour window and the WhatsApp template rule are also gone.
- **Fix:** In FR-24 and FR-47:
  - create a WhatsApp Draft only when the prospect has shared a number *and* opted in, and record the opt-in in Evidence and the Provenance Record;
  - create an Instagram Draft only within 24 hours of the prospect's own message;
  - show the rule that applies and the relevant timestamp on the Draft card.

### R-11 · Medium · Weakened: LinkedIn assist (whose account sends, and the ~20-a-day queue may run dry)
- **Source:** brief.md L36 ("Furqaan's name is on every message"); L61 ("a daily queue of about 20 decision-makers with drafted notes"); L114 (prerequisite: an updated LinkedIn profile for Furqaan); brief-add §G L100 (Furqaan's personal LinkedIn is "a sales asset").
- **PRD:** Glossary L90 (a LinkedIn Task is a to-do for the Operator); UJ-3 L55 ("for Mac to send by hand"); FR-24 L227 (only Contacts with a known LinkedIn URL); FR-48 L341; FR-49 L345 (LinkedIn URLs come only from the Signal Inbox, the Lead's website or the Operator).
- **Gap:**
  - (a) The PRD never says whose LinkedIn account sends. If Mac sends from his own profile, the message isn't in Furqaan's name. If he sends from Furqaan's account, that is account sharing, and it puts at risk the "sales asset" the brief protects.
  - (b) A Task needs a known LinkedIn URL. The engine may not fetch LinkedIn, and few firm websites list partners' LinkedIn URLs, so the brief's ~20-a-day queue will be mostly empty.
- **Fix:** Either state that Furqaan sends LinkedIn Tasks from his own account (Mac prepares them), or decide explicitly that Mac sends from his own profile with copy in his own voice. Allow a Task without a URL: give a search hint (name, company and role) so the human finds the profile, then log the URL back (allowed under FR-49 as Operator-provided).

### R-12 · Low · Silent: the warm-up decision is partly closed
- **Source:** brief-add §F L93 lists three options, including engine-run warm-up between the Nextron inboxes; brief-add §A.6 L21.
- **PRD:** §6.2 L431 (warm-up exchange automation is out of the MVP, "decision deferred"); FR-33 L272; OQ1 L471 ("engine-run in v2").
- **Gap:** The PRD removes the engine-run option from v1 while still calling the decision deferred. Neither memlog records this.
- **Fix:** Log it as a decision, or keep engine-run open until OQ1 is answered. Fix the OQ1 timing at the same time (see R-04).

### R-13 · Low · Dropped: details of the Offer
- **Source:** brief.md L67–69: the cold-email ask is a reply; the audit is pitched "on the call", is "fixed-fee" and is "credited in full toward a build".
- **PRD:** Glossary L91 (the Offer ladder, without "fixed fee" or the credit); FR-43 L318 (Reply Drafts "answer… using the Offer").
- **Gap:** Reply Drafts can't mention the credit because it isn't in the Offer. The PRD also doesn't say whether Reply Drafts may quote audit prices by email, or should hold them for the call as the brief's order implies.
- **Fix:** Add "fixed fee, credited in full toward a build" to the Offer. Add a rule to FR-43: Reply Drafts push for the call and quote prices only if the prospect asks (or the reverse, if that is the intent).

### R-14 · Low · Weakened: success-metric definitions drift
- **Source:** brief.md L75 ("Paid engagement signed", which L18 and L86 frame as a $10k–$30k first project and "about 1 client a month"); L77 ("positive", where the brief's reply classes at L58 are interested, not now, unsubscribe and out of office); L80 ("Approval takes under 30 minutes a day").
- **PRD:** SM-1 L451 ("Signed audits or builds"); SM-3 L455 (positive means Interested, Question or Referral); SM-6 L458 ("**Median** daily approval time of 30 minutes or less").
- **Gap:**
  - SM-1 lets a $2,500 audit count toward the headline goal.
  - SM-3 widens "positive", which makes the 1.5% target easier to hit than in the brief.
  - SM-6 uses a median, so half of all days could exceed 30 minutes.

  Each choice can be defended, but none is flagged as a change.
- **Fix:** Confirm each with Mac and Furqaan:
  - SM-1: keep counting audits, and add a separate measure for builds.
  - SM-3: define positive as Interested (plus Referral) and report Question on its own, or confirm the wider definition.
  - SM-6: use "on 90% of working days" instead of the median.

### R-15 · Low · Dropped: the open-tracking caveat
- **Source:** brief-add §A.5 L20 ("Leave open tracking off… If it is ever turned on, add a custom tracking domain first").
- **PRD:** §5.5 L417 ("Default to plain text, with no open tracking pixels and no link tracking").
- **Gap:** "Default" implies tracking can be switched on, but the precondition (a custom tracking domain first) isn't carried over.
- **Fix:** Either make it "no open or link tracking in v1, not configurable", or add the custom-domain precondition.

### R-16 · Low · Silent: Segment wording drifts
- **Source:** brief.md L18 and L42 ("**light** manufacturers").
- **PRD:** §1 L35 ("distributors, logistics firms and manufacturers"); FR-1 L100 keeps "light".
- **Gap:** §1 widens the Segment to all manufacturers.
- **Fix:** Add "light" to §1.

---

## 2. Scope the PRD adds beyond the brief

### A-01 · Medium · An in-dashboard editor for everything, "without code changes"
- **Source:** brief.md L20 (the scope rule); L103 and Vision L126–128 (other Segments and Markets come in later waves).
- **PRD:** §4.1 L96 ("Everything is editable without code changes"); FR-1 to FR-4; §6.2 L436 (adding Segments and Markets "without code changes").
- **Gap:** A dashboard editor for Segments, Markets, Campaigns, Variants, Offers and Sequences serves the Vision, not speed to the first client. v1 needs only two pre-configured Segments in two Markets.
- **Fix:** In v1, keep Segments, Markets and Market rules as version-controlled config files that Mac edits. Limit dashboard editing to Step writing briefs, Variants and pausing or resuming Campaigns. Keep the data model extensible.

### A-02 · Medium · Approval machinery for every follow-up (logged)
- **PRD:** FR-26 L236 (follow-ups queued as they fall due); FR-30 (batch approval); FR-31 L261 (approvals expire after 3 days); OQ7 L477. PRD .memlog L12.
- **Gap:** These follow from the change in R-02. None is needed under the brief's approve-then-auto-send model.
- **Fix:** Settle R-02 first. If Sequence-level approval is adopted, FR-30 and OQ7 fall away and FR-31 shrinks to Step 1.

### A-03 · Low (justified) · UK GDPR operations beyond the brief's compliance basics
- **Source:** brief.md L97 (five compliance basics).
- **PRD:** FR-52 L358–359 (erasure and export); FR-53 L361–362 (12-month retention); §5.1 L396–397 (a legitimate-interest basis per Contact, and a written legitimate-interests assessment (LIA) per Campaign as a business prerequisite); OQ6 L476.
- **Gap:** This is very likely the right call under UK GDPR, but it is still added scope. The LIA is a new gate before UK sends that isn't in the brief's prerequisites.
- **Fix:** Keep it. Add the LIA to the prerequisites table (R-04). Tag the FR-52 export and the FR-53 retention job as "First month" rather than "First send".

### A-04 · Low (logged) · Alerts through Telegram, email and the dashboard
- **Source:** brief.md L58 says only that interested leads "go to Furqaan" and names no channel.
- **PRD:** FR-42 L314; FR-56 L378; OQ4 L474. PRD .memlog L11 (decision).
- **Gap:** A third integration, plus a bot token and chat ID to set up beforehand.
- **Fix:** Keep it, and add the Telegram setup to the prerequisites. If Telegram setup lags, go live with email alerts alone.

### A-05 · Low–Medium · The pipeline creeps toward a CRM
- **Source:** brief.md L107 (out of v1: "A full CRM; a simple pipeline view is enough"); L59 (the funnel covers sent, bounced, replied, positive, call booked and won).
- **PRD:** Glossary L87 (12 Pipeline Stages); FR-45 L328–329 (deal value and notes); FR-46 L331–332 (filters and full history); FR-47 L335 (logged touches); FR-54 L368 ("won value").
- **Gap:** Twelve stages, deal values, notes, touch logs and won value go beyond "a simple pipeline view".
- **Fix:** Keep the stages the funnel and SM-1/SM-2 need: Contacted, Replied, Interested, Call Booked, Audit Won, Won, and Lost or Disqualified. Move deal value, notes and touch logging to "Later v1".

### A-06 · Low · More Reply Classes and a wider Hot Lead definition
- **Source:** brief.md L58 (interested, not now, unsubscribe, out of office).
- **PRD:** Glossary L84–86 (9 classes; a Hot Lead is Interested, Question or Referral); FR-40 L304–307.
- **Gap:** A sensible addition, but it changes the positive-reply metric (R-14) and the number of alerts.
- **Fix:** Keep it, and tie the class definitions explicitly to SM-3.

### A-07 · Low · New metrics and counter-metrics
- **PRD:** SM-7 L459; SM-8 L460; SM-C1 to SM-C4 L464–467; FR-55 L371–372 (approval metrics).
- **Gap:** None of these is in the brief. SM-8 (under 2 business hours) can't be met as the PRD now specifies the hand-off (R-03). SM-C3 contradicts SM-4 (N-09).
- **Fix:** Keep SM-7. Fix SM-8 through R-03 and SM-C3 through N-09.

### A-08 · Low · Operational extras, with one internal conflict
- **PRD:** §5.3 L408 (daily backup plus an optional copy to a cloud folder); §5.4 L411 ("All data stays on the local PC"); §5.6 L423 (prompt versioning); FR-57 (quota and cost meter); FR-58 (health panel); FR-11 (signal decay); FR-19 (minimum Lead Score).
- **Gap:** Most of these support the brief's constraints on cost and reliability. However, the optional cloud-folder backup copies prospects' personal data off the PC, which contradicts §5.4 and brushes against the brief's "no cloud hosting".
- **Fix:** Keep the extras, but choose between the cloud-folder backup and §5.4's local-only rule. Tag prompt versioning and approval metrics "First month".

### A-09 · Low · Wider decision-maker roles for Segment 2
- **Source:** brief.md L42 ("COO or VP Operations").
- **PRD:** FR-1 L100 ("COO, VP Operations, owner, CEO").
- **Gap:** A small widening that is probably right for smaller distributors, but it changes who receives email.
- **Fix:** Confirm it, and rank the roles: COO or VP Operations first, owner or CEO as the fallback.

---

## 3. Numeric inconsistencies

### N-01 · High · The *Likely* cap and the credit budget together cap new Contacts at about 10 a day
- **Source:** brief.md L83–86 (about 90 emails a day reaching 500–600 prospects a month); L121 (Hunter: 50 credits a month); brief-add §C L53 (Hunter 50 plus Prospeo 100 credits a month, used for "email finding **and** verification"); brief-add §E L87 (Hunter Starter at $34 a month as a headroom option).
- **PRD:** FR-14 L166 (Hunter and Prospeo used for *finding*); FR-15 L176–179 (*Verified* needs an API result; addresses published on a website count as *Likely*; *Likely* is capped at 30% of daily sends); FR-19 L203 (20–25 new Contacts a day); prd-add §4 L42 (counts all 150 credits as verification and says "most addresses will be *Likely*").
- **Gap:** A Contact keeps the same address for all four Steps, so the *Likely* share of sends roughly equals the *Likely* share of active Contacts.
  - A 30% cap means at least 70% of new Contacts need a *Verified* address.
  - At most 150 credits arrive a month, and finding uses some of them, so there are at most about 150 *Verified* addresses a month.
  - That allows at most about 214 new Contacts a month: about 10 a weekday, or about 40 sends a day.

  That is under half the 90-a-day capacity and about 40% of the brief's 500–600 prospects. The addendum's own statement that "most addresses will be *Likely*" can't coexist with a 30% cap.
- **Fix:** Choose one, then restate the FR-19 capacity assumption:
  - (a) make addresses published on a website that pass the MX check a separate tier outside the cap, and cap only unverified pattern-guessed addresses;
  - (b) replace the fixed 30% with an adaptive cap based on the trailing 7-day bounce rate;
  - (c) fund Hunter Starter ($34 a month) from the brief's headroom and re-check the budget table.

### N-02 · High · The Warm-up Ramp re-ramps warmed inboxes and is slower than the brief
- **Source:** brief-add §A.6 L21 (warm up over 2–4 weeks, from 5–10 a day to 25–30 a day); §A.7 L22 (*then* cap cold sends at 30 per inbox per day for the first month); brief.md L110 and L118; brief .memlog L18 (warm-up overlaps the build).
- **PRD:** FR-33 L271 (the cold-send Daily Cap follows the ramp: starts at 5 a day, rises by about 5 a week, tops out at 30); Glossary L80–81.
- **Gap:**
  - (1) At 5 more a week, starting from 5 a day, the ramp reaches 30 a day only in week 6 (5, 10, 15, 20, 25, 30), against the brief's 2–4 weeks.
  - (2) The brief warms inboxes *before* cold sending, during the build. The PRD applies the ramp to the *cold-send* Daily Cap, so an inbox already warmed during the build starts cold sending again at 5 a day. Over the first 6 weeks that gives about 1,575 cold sends instead of about 2,700 at 90 a day: about 42% fewer, and all inside the 90-day window for the first client.
- **Fix:** Let each inbox's ramp start from the warm-up already done, for example with an Operator-set "warmed since" date or current cap. For inboxes that weren't pre-warmed, finish the default ramp within 4 weeks (for example, 10 to 30 over 3 weeks). Keep the 30-a-day cap for the first month, as brief-add §A.7 says.

### N-03 · Medium · Prospects a month: 500–600 in the brief against about 440–480 in the PRD's capacity model
- **Source:** brief.md L84–86: 1,800 sends → 500–600 prospects → 20–30 replies → 8–10 positive → 4–5 calls → about 1 client.
- **PRD:** FR-19 L203 (20–25 new Contacts a day); prd-add §4 L39–40 (22 new Contacts plus 68 follow-ups a day; 1,800 a month assumes 20 weekdays); §10 L484.
- **Gap:** With a four-Step Sequence, 1,800 sends reach about 450 Contacts (about 470 with early stops), not 500–600. The PRD silently uses the lower figure but never re-runs the funnel. At 450–480 Contacts the brief's rates give about 18–19 replies, about 7 positive and 3–4 calls, below SM-2's target of 4+ a month unless the rates beat industry averages. (Using about 21.7 weekdays a month gives about 1,950 sends, which partly offsets this.)
- **Fix:** Add the funnel at the PRD's capacity to §8 or prd-add §4, and state that SM-2 assumes above-average rates. If needed, consider a fourth inbox (about $7 a month). Correct the brief's funnel through the change log.

### N-04 · Medium · Bounce target of 2% against an auto-pause at 3%
- **Source:** brief.md L79 (bounces under 2%).
- **PRD:** FR-38 L296 (pause when the 7-day bounce rate exceeds 3%); §5.5 L418 ("Targets: bounce rate under 2%… (FR-38 enforces a hard stop)"); SM-5 L457.
- **Gap:** The hard stop sits above the target, so the target can be missed with no action. §5.5's wording implies the stop enforces the 2% target, which it doesn't.
- **Fix:** Use two thresholds: at a 2% 7-day bounce rate, alert and throttle *Likely* sends; at 3%, pause the inbox. Require a minimum sample (for example, 50 sends or more) before either triggers. Reword §5.5.

### N-05 · Medium · Variants: two per Segment each month against two per Campaign
- **Source:** brief-add §D L71 ("two variants per segment each month").
- **PRD:** FR-3 L109–110 (up to two Variants per *Campaign*, where a Campaign is a Segment × Market pairing); Glossary L73.
- **Gap:** The PRD allows up to four live Variants per Segment, with no monthly rotation. At about 450 Contacts a month across 4 Campaigns × 2 Variants, each Variant gets about 55 Contacts a month. At a 1.5% positive rate that is less than one positive reply per Variant, so comparisons will be noise.
- **Fix:** Scope Variants to the Segment, shared across the US and UK, and rotate them monthly as the brief says. State a minimum sample size before declaring a winner.

### N-06 · Medium · AI cost basis: about 600 leads in the brief against 1,000–1,500 enrichments in the PRD
- **Source:** brief-add §E L79 (Claude API about $10–25 a month for about 600 leads; "size in the architecture step").
- **PRD:** prd-add §4 L41 (50–70 candidates a day, 1,000–1,500 a month enriched); prd-add §3 L33 (a second AI pass per Draft for the grounding check); FR-20 (all Steps drafted up front); FR-24, FR-25, FR-40 and FR-43 (more AI calls); FR-57 L380–383 (a monthly AI budget, with no default value); §5.2.
- **Gap:** The PRD's AI workload is about 2–2.5 times the brief's sizing basis, plus extra passes, so the $10–25 line and the $35–50 total may not hold. With no default value, FR-57 has nothing to enforce.
- **Fix:** Give FR-57 a default monthly AI budget (for example, $30) with an alert at 80%. Pre-score candidates cheaply before full enrichment. Re-size the budget in the architecture step using the PRD's volumes.

### N-07 · Medium · A 08:00–17:00 Send Window against confirmed "morning" PC uptime; the PC's timezone isn't recorded
- **Source:** brief.md L120 ("on during US and UK **business hours**"); brief-add §F L94 (where the PC is is an open decision; it mentions "US and UK **morning** hours"); brief .memlog L26 ("on during US and UK **morning** sending windows").
- **PRD:** Glossary L82 (the default Send Window is 08:00–17:00 on weekdays); prd-add §1 L12 ("morning Send Windows"); FR-34 L275 (gaps of 4–12 minutes per inbox); UJ-1 L53 and UJ-5 L57, which both assume a PC timezone.
- **Gap:** The brief contradicts itself ("business hours" against "morning"), and the PRD inherits both versions.
  - A full 08:00–17:00 local window across the UK and US Eastern to Pacific covers about 17 hours of PC time (08:00 UK to 17:00 PT), more than the confirmed uptime.
  - At 4–12-minute gaps, 30 sends per inbox need 2–6 hours (about 4 on average).
  - Where the PC is located is still open, yet UJ-5's "slept from 11 pm to 9 am" assumes a particular timezone.
- **Fix:** Record the PC's timezone and the confirmed uptime hours. Set the default Send Window to match (for example, 08:00–12:00 in the recipient's time) or state the longer uptime as a requirement. Check that 30 sends per inbox fit inside the window at the configured gaps.

### N-08 · Low · An alert SLA of 5 minutes against polling every 5 minutes
- **Source:** these numbers are new in the PRD; the intent comes from brief.md L58.
- **PRD:** FR-39 L302 (checks each inbox at least every 5 minutes); FR-42 L314 (alerts within 5 minutes); UJ-2 L54.
- **Gap:** In the worst case, polling lag alone uses the full 5 minutes before classification even starts, so the SLA can't be met as written.
- **Fix:** Poll every 1–2 minutes, or set the alert SLA to 10 minutes after the Reply arrives.

### N-09 · Low · Reply rate is both a target and a "do not optimize" counter-metric
- **Source:** brief.md L78 (total reply rate of 4%+, "Shows message quality").
- **PRD:** SM-4 L456 (a 4% target); SM-C3 L466 ("Raw reply rate… Counterbalances SM-4").
- **Gap:** The same measure is set to counterbalance itself.
- **Fix:** Replace SM-C3 with a measure of negative replies, such as the share of replies that are Unsubscribe or Not Interested, or the spam-complaint rate.

### N-10 · Low · The Places quota arithmetic is unstated
- **Source:** brief-add §C L48 (free tiers: 5,000 Pro and 1,000 Enterprise requests a month).
- **PRD:** prd-add §4 L41 (1,000–1,500 candidates a month, "within… 1,000 Places Enterprise calls a month, or more with Pro-only field masks"); FR-5 L120–121 (needs each candidate's website URL).
- **Gap:** 1,500 candidates fit within 1,000 calls only if each Text Search call returns several places, and the addendum doesn't say so. FR-5 needs a website URL for every candidate, so the "Pro-only" path holds only if the Pro tier returns that field.
- **Fix:** State the assumed results per call and the SKU that returns the website field. Verify both in the architecture step, alongside OQ5.

### N-11 · Low · LinkedIn: about 20 a day against 100 a week
- **Source:** brief.md L61 (about 20 a day); brief-add §B L29 (at most 100 invites a week).
- **PRD:** FR-48 L341 (a default of 20 a day, at most 100 in a rolling week); FR-24 L227 (connection note of 300 characters or fewer).
- **Gap:** The two limits agree only for a 5-day week, and FR-48 doesn't say weekdays. A 7-day list would hit the weekly cap by day 5. Separately, and outside the brief: free LinkedIn accounts reportedly get a shorter note limit and only a few personalized invitations a month. That should be verified for the account that will actually send, because it could cut the brief's LinkedIn assist plan sharply.
- **Fix:** Change FR-48 to "20 per weekday". Confirm the note length and monthly allowance for personalized invitations on the sending account (see R-11).

---

## 4. Checked and consistent

These match between the brief and the PRD:
- **Offer prices:** an audit at $2,500–$3,500 and a build at $10k–$30k (brief L68–69; PRD Glossary L91, §10 L488). The PRD also keeps the brief's tag to validate the Offer on the first 10 calls.
- **Running cost:** a cap of under $100 a month, with about $35–50 expected (brief L81; PRD §5.2 L402, SM-9).
- **Sending caps:** 30 per inbox per day; 3 inboxes give 90 a day, about 1,800 a month (brief-add §A.7; FR-33, prd-add §4).
- **Sequence:** four emails over about 14 days, on days 0, 3, 7 and 14 (brief-add §D L69; FR-4).
- **Step 1 rules:** under 80 words, no links except the signature, one CTA and no attachments (brief-add §D L68; FR-4, FR-21).
- **LinkedIn:** at most 100 invites a week (brief-add §B L29; FR-48).
- **Opt-outs:** honoured immediately; CAN-SPAM's limit is 10 business days (brief-add §B L28; §5.1 L395).
- **Segment sizes:** 5–50 lawyers, and $10M–$200M revenue (brief L42; FR-1).
- **Success targets:** first client within 90 days, then at least 1 a month; 4+ calls a month from month 2; 1.5% positive replies; 4% total replies; bounces under 2%, complaints under 0.1%, no blocklisting (brief L75–79; §8). See R-14 for definition drift.
- **Markets:** US and UK only; in the UK, corporate subscribers only, with legal form checked on Companies House; every other country blocked (brief L97 and L102, brief-add §B; FR-2, FR-12, FR-18).
- **Sending domain:** never send cold email from `nextronai.com` (brief-add §A.1; FR-32, §5.5).
- **Drafting:** evidence-only drafting, with a flagged generic fallback (brief-add §D L67; FR-20 to FR-22).
- **Downtime:** safe catch-up with no bursts and no duplicates (brief L120; FR-35, FR-36, UJ-5).
- **Exclusions:** no LinkedIn or Instagram automation or scraping, no cold Instagram or WhatsApp messages, no automated calling, no paid databases beyond free tiers, and no multi-user access or cloud hosting (brief L99–107; §6.2, §7, apart from A-08).
- **Intent:** "not scan every business" survives as §7 "Not a mass-email tool" and SM-C1 and SM-C2. The PRD's "deliberately narrow and careful" framing (§1 L35) matches the brief's intent.

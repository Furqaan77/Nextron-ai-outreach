# PRD Quality Review — Nextron Outreach Engine

## Overall verdict
This is a disciplined, specific PRD. The Vision, the Non-Goals, the counter-metrics and the deterministic sending pipeline (Eligibility, Daily Caps, Send Windows, exactly-once sending, suppression) are ready to build, and the Glossary and IDs will extract cleanly into architecture and stories. The risk is in what the thesis depends on:
- the AI-judgment gates (grounding, Signal Evidence, reply classification, claims about Nextron) have no definition of done;
- by the PRD's own numbers, the verification budget and the 30% *Likely* cap hold sending to under half the planned volume;
- the hot-reply path, where revenue actually comes from, quietly depends on Mac being at the PC.

There are no critical findings. Fix the 7 high findings, most of which need only a paragraph each, before architecture starts.

## Decision-readiness — adequate
The big product decisions are stated as decisions and have owners:
- every outbound message needs approval (§7, "Not autonomous");
- the dashboard is localhost-only (§5.4);
- LinkedIn and Instagram are never automated (FR-49);
- cold email goes out only from lookalike domains (FR-32);
- warm-up is out of scope, with a revisit trigger (§6.2).

Open Questions have owners and deadlines ("*Owner: Furqaan; needed before the first send.*"). That is better than most PRDs.

The PRD gets vague where those decisions affect each other. Localhost-only, approve-everything, and a PC confirmed on only "during US and UK morning Send Windows" (addendum §1) all meet at the hot-reply moment, and UJ-2 skips past it: "The next time Mac opens the dashboard". The verification budget and the *Likely* cap both limit volume, and the addendum states both without noticing the clash: "most addresses will be *Likely*… This is why FR-15 caps *Likely* sends." The PRD's only `[NOTE FOR PM]` sits on a safe v2 idea rather than on either of these tensions.

### Findings
- **high** The hot-reply path depends on PC uptime and on Mac being at the PC, and the PRD doesn't say so (§2.1 Sender JTBD, UJ-2, FR-39, FR-42, FR-43, FR-31, SM-8). Furqaan's job is "Tell me immediately… so I can get them onto a call while they're warm", but he can't act on the alert.
  - The dashboard is localhost-only (§5.4), and every Reply Draft needs approval at the PC (FR-43). UJ-2 skips past this: "The next time Mac opens the dashboard, he finds a drafted reply."
  - Polling and alerts run only "while running" (FR-39). The PC is confirmed on only "during US and UK morning Send Windows" (addendum §1), so a reply that arrives in a US afternoon may not be seen until the next day.
  - SM-8's "Median under 2 business hours" doesn't say whose business hours.
  - Nothing covers Mac being away. After 3 days, FR-31 sends every approved email back to the queue and Sequences stall.

  *Fix:* add a `[NOTE FOR PM]` and choose a path:
  - (a) let Furqaan reply by hand from the Sending Inbox in Gmail, and have the engine detect outbound messages in the thread so it stops the Sequence, logs the touch, moves the Pipeline Stage and records SM-8; or
  - (b) accept the delay and say so.

  Either way, state the PC uptime needed for US afternoons (ET to PT), define when SM-8's clock starts and stops, and add a rule for when Mac is away.
- **high** By the PRD's own numbers, the verification budget and the *Likely* cap limit sending to about 40 emails a day, not 90 (FR-15, FR-19, addendum §4).
  - Published and pattern-derived addresses stay *Likely* unless an API verifies them (FR-15).
  - Verification credits total "150… a month combined" (addendum §4), so at most about 7 new *Verified* Contacts per weekday.
  - At 4 Steps each, that is about 28 *Verified* sends a day.
  - With *Likely* capped at "30% of daily sends", total sends top out near 28 ÷ 0.7 ≈ 40 a day. That is about 10 new Contacts a day, against FR-19's "about 20–25" and the 90-a-day model in addendum §4.
  - At 10 new Contacts a day and SM-3's 1.5% positive reply rate, SM-2's "4+ a month" calls is out of reach.

  *Fix:* state this trade-off and pick one option:
  - raise the cap and rely on FR-38 as the backstop;
  - put addresses published on the Lead's own site in a higher-trust class outside the cap;
  - pay for Hunter Starter out of the budget headroom (brief addendum E);
  - or lower the volume and SM-2 targets.

  Then update the FR-19 assumption and addendum §4 so they agree.
- **medium** FR-30 quietly reinterprets Mac's approval decision (§4.7, FR-30, OQ-7). §4.7 says every email "needs explicit Operator approval before sending (Mac's decision)". FR-30 then adds batch approval and declares that it "satisfies 'approve every email'". OQ-7 asks whether batch approval "should… become the default", which FR-30 has already half-answered. An architect can't tell whether batch approval is in v1 scope or is the fallback OQ-7 is weighing. *Fix:* flag it as `[NOTE FOR PM]` and decide whether it is in v1. If it is, say what the compact list shows: the full body and Evidence, or the subject line only.
- **medium** The PRD never says whose LinkedIn account sends the LinkedIn Tasks (FR-24, FR-48, UJ-3, Glossary "LinkedIn Task").
  - Every email is "in Furqaan's name" (§1), but UJ-3 has Mac send the connection note ("for Mac to send by hand"). The brief calls Furqaan's LinkedIn "a sales asset" (brief addendum G).
  - Prospects would get an email from Furqaan and a connection request from Mac. Mac sending from Furqaan's account would mean sharing a login, which LinkedIn's user agreement forbids.
  - The choice also sets the voice of the drafts and the length limit. FR-24's "≤300 characters" assumes a paid account. Free accounts get shorter notes and a monthly cap on personalized invitations (check the current limits).

  *Fix:* name the sending account, per Segment or Market if needed, and draft in that person's voice within that account's limits.
- **low** The only `[NOTE FOR PM]` is on a safe idea, and three Open Questions are really setup tasks (§6.2, §9). "A strong v2 conversion idea" is not a tension. OQ-3 ("create a Cal.com account and an event type"), OQ-4 and OQ-6 are to-dos with nothing to decide. *Fix:* move OQ-3, OQ-4 and OQ-6 to a pre-launch checklist line, and put `[NOTE FOR PM]` on the two high findings above and on FR-30.

## Substance over theater — strong
- **Users:** three jobs-to-be-done (Operator, Sender, Business), and each one drives specific requirements: FR-29's keyboard speed, FR-42's three-channel alert, FR-57's cost meter, and the configurable Segments and Markets in FR-1 and FR-2. None of it is filler.
- **Vision:** it could not be dropped into another PRD. It names the two Segments, the two Markets, the channels the engine refuses to automate, and a dated definition of success.
- **Non-functional requirements:** they use thresholds, not adjectives: "bounce rate under 2%", "never send outside a Send Window", credentials "never in plain-text config files or logs".
- **Counter-metrics:** the strongest part of the document. SM-C3 ("Provocative copy can raise replies through angry or 'stop' responses") and SM-C4 ("Rubber-stamping defeats the human checkpoint") name failure modes specific to this product.
- No differentiation section was written just to fill a template slot.

No findings. The little repetition there is (for example, §5.5 repeating SM-5) is a length issue and is covered under Shape fit.

## Strategic coherence — adequate
The thesis is clear and the features serve it: narrow Segments, copy grounded in Evidence, human approval and hard compliance limits, judged by paid engagements (SM-1) and booked calls (SM-2), with real counter-metrics. §4 runs in funnel order from setup to analytics, so it doesn't read like a backlog.

Two things undercut it:
- **No priorities.** §6.1 puts "All of §4" in scope, which is 58 FRs of equal weight. For a business with no clients, every week before the first email goes out matters.
- **No check on Signal supply.** The PRD never checks that its sources can produce the Signals the thesis depends on, and its scoring lets Leads with no Signal through. The engine could end up sending mostly generic template emails without anyone noticing.

### Findings
- **high** v1 has no first-send slice or build order (§6.1, §4). The whole MVP is "All of §4". For a business with no clients, the path that matters most is the time to the first cold email, and the Warm-up Ramp already adds about 6 weeks before full volume: FR-33 starts at 5 emails a day and rises "by about 5 a week" to 30. Several features aren't needed for the first send:
  - FR-7 (hiring-signal discovery) and FR-8 (Signal Inbox);
  - FR-24 (channel Drafts) and FR-25 (regenerate);
  - FR-30 (batch approval);
  - FR-45 to FR-48 (pipeline, warm-channel follow-up, LinkedIn Tasks);
  - FR-54 and FR-55 (analytics);
  - the editing screens in FR-1 to FR-4 (the defaults could ship as configuration).

  Epics will treat all of these as equal to the sending path. *Fix:* split §6.1 into a "First-send slice" and "Complete v1", and add a target date for the first send relative to inbox warm-up.
- **high** The hiring-Signal sources probably can't feed the thesis (FR-7, §4.2, addendum §2, brief addendum C).
  - FR-7 is filed under Lead Discovery, but the Greenhouse, Lever and Ashby job-board endpoints are queried one company at a time, using that company's board name or token. You can't search them for "data entry clerk" across companies, so they can't discover new Leads.
  - The target firms (law firms with 5–50 lawyers; distributors and manufacturers with $10M–$200M revenue) probably rarely use these applicant-tracking systems, which mostly serve tech companies.
  - Hiring is the headline Signal for both Segments (brief addendum C). In practice it will come from careers-page crawling (FR-10) and manual pastes into the Signal Inbox.

  *Fix:*
  - move FR-7 into §4.3 as enrichment of a Lead the engine already knows;
  - add an `[ASSUMPTION]` for the expected share of Leads with at least one automatically found Signal;
  - decide whether a job-aggregator source is in scope, and under what terms of use.
- **medium** The scoring lets in Leads with no Signal, which contradicts step 1 of the Vision (FR-17, FR-19, FR-22, §1). §1 says the engine "finds… companies showing public evidence of a problem Nextron solves". But Lead Score is 0.5 × Fit + 0.5 × Signal and FR-19 only rejects Leads "below… 50". A Lead with Fit 100 and Signal 0 scores exactly 50, gets selected, and then receives FR-22's generic template. Nothing measures how often this happens. *Fix:* either require a minimum Signal Score (or at least one Signal) for automatic selection, or state plainly that generic outreach is an accepted path. Either way, add "share of Step 1 Drafts flagged generic" to FR-54, with a counter-metric to watch it.
- **medium** No rule links the metrics to action (§8, §9, FR-54). SM-1 sets a 90-day target and FR-54 shows the funnel by Segment, Market and Variant. But the PRD never says which result should trigger a change of Variant, Segment or Offer, or what happens at day 90 if there is still no client. Only the Offer has a test point ("to be validated on the first 10 calls", §10). *Fix:* add 2–3 checkpoints. For example: "After about 300 Contacts in a Campaign, if the positive reply rate is below 0.5%, replace the Variant. After two failed Variants, review the Segment or the Offer."
- **low** SM-7 can't be measured from what FR-55 records (§8 SM-7, FR-55). SM-7 counts Drafts "approved without a regenerate or major edit", but FR-55 records only approval time and rejection reasons, and "major edit" isn't defined. *Fix:* have FR-55 record how much each Draft was edited (for example, the share of characters changed), and define "major" (for example, more than 20%).

## Done-ness clarity — adequate
The verdict is split. The deterministic pipeline is strong, but the AI-judgment core is thin.

Most FRs have defaults and conditions a tester can check. Examples include FR-2, FR-4, FR-15's address labels, FR-18, FR-19, FR-23, FR-31, FR-33 to FR-37, FR-48, FR-50 and FR-57:
- "Step 1 must stay under 80 words"
- "default 4–12 minutes per inbox"
- "goes back to the Approval Queue for re-approval"

FR-35's exactly-once rule, which checks the Sent folder before any retry, is a model requirement.

The FRs where "done" is unclear are the ones that carry the brand and compliance risk:
- what counts as passing the grounding check (FR-21);
- what Evidence looks like for Signals that are an absence or a count (FR-11);
- what the AI may claim about Nextron itself (FR-43);
- how accurate reply classification must be before it triggers suppression and alerts (FR-40).

Story writers will have to invent these bars themselves.

### Findings
- **high** The AI-judgment gates have no acceptance bar (FR-21, FR-40, FR-41).
  - FR-21 relies on the AI to catch "fake familiarity, made-up metrics, false urgency" and claims without Evidence.
  - FR-29's target of 90 items in about 30 minutes leaves Mac roughly 20 seconds per card, so this check carries most of the brand risk. Nothing says how good it has to be.
  - FR-40 says "Uncertain classifications are flagged" but gives no threshold and no safe default.
  - A missed *Unsubscribe* is a compliance breach (FR-41 covers "any reply clearly asking not to be contacted"), and a missed *Interested* is lost revenue.

  *Fix:*
  - Build a test set of sample Drafts and Replies with known answers, and set pass marks. For example: the grounding check catches at least 95% of planted unsupported claims, and *Unsubscribe* recall is effectively 100%, backed by a rule-based check for opt-out phrases.
  - Set safe defaults: any opt-out wording means suppress; any doubt about whether a Reply is Hot means alert as Hot.
- **high** The Evidence model can't hold Signals that are an absence or a count (Glossary "Evidence", FR-11, FR-21).
  - Evidence is defined as "source URL, quoted text excerpt, retrieval time", and FR-11 requires "a verbatim excerpt of up to 300 characters".
  - Several Signals in the brief can't be quoted. For law firms: "Contact form only (no chat, booking or after-hours line)" and "5–50 attorneys on the team page". For operations firms: "B2B order form or PDF catalog instead of a portal". Each is an absence or a count.
  - FR-21 would flag any Draft that cites them in red, which removes one of the law Segment's best hooks. The other outcome is that the engine pastes a meaningless excerpt just to pass the check.

  *Fix:* give Evidence types (a quote; an observed absence, listing the pages checked; a count, with how it was counted) and say how FR-21 checks each type.
- **high** Nothing governs what the AI claims about Nextron (FR-21, FR-43, FR-4).
  - FR-21 checks only "claims about the Lead".
  - FR-43 answers prospects "using the Offer and approved company facts", and that term is not defined anywhere.
  - FR-4 lets each Step define its "allowed content", and the brief's message guidelines want follow-ups to include "a relevant case study" (brief addendum D).
  - Nextron has no clients. Any case study, client count or result the model writes is invented, and it goes out under Furqaan's name.

  *Fix:*
  - add a Glossary term, for example "Company Facts": a list of approved claims, prices and proof points that the Operator maintains, plus an FR for managing it;
  - extend FR-21 to flag any claim about Nextron that isn't on that list;
  - ban case-study claims until a real case study exists.
- **medium** FR-38's complaint and blocklist detection has no data source (FR-38, §5.5, SM-5, addendum §2).
  - The hard stop fires on "any spam complaint or blocklist signal", but no integration in addendum §2 provides either one.
  - Business recipients on Google Workspace or Microsoft 365 don't send complaint reports back to senders. Gmail Postmaster Tools usually shows no data at this engine's volume.
  - So SM-5's "spam complaints under 0.1%" can't be measured as written.

  *Fix:* name the sources: scheduled DNS blocklist lookups for each Sending Domain, treating "stop spamming me"-type Replies as complaints, and Postmaster Tools if it ever shows data. Otherwise, rewrite SM-5 to cover only what can be measured.
- **medium** FR-29's only real performance limit is in the addendum (FR-29, addendum §3). FR-29 says "the next card loads straight after a decision". The actual limit ("renders in under 300 ms") is in an addendum that describes itself as "recommendations and constraints, not decisions". *Fix:* move "under 300 ms from decision to next card" into FR-29, and replace "90 straightforward items… about 30 minutes" with SM-6's bound.
- **low** Several qualifiers are loose, and two timings conflict (FR-16, FR-22, FR-28, FR-11, FR-39/FR-42, FR-4).
  - "several" bounces (FR-16) and "no Signal strong enough" (FR-22) have no value.
  - FR-28's "skip" has no stated result.
  - FR-11 measures a Signal's age from its "detection date", so a two-year-old job post found today counts as fresh.
  - FR-39 checks inboxes "at least every 5 minutes", but FR-42 promises alerts "within 5 minutes", so the worst case misses the promise.
  - FR-4's "days 0, 3, 7 and 14" doesn't say what happens when a Step falls on a weekend.

  *Fix:* give each a configurable default. Measure a Signal's age from the date of the underlying fact when it is known. Either check inboxes more often (for example, every 2 minutes) or measure the alert promise from when the Reply is detected.

## Scope honesty — adequate
The Non-Goals (§7) and Out of Scope (§6.2) sections do real work. "Not autonomous: no outbound message of any kind goes out without Operator approval" closes a door that architecture might otherwise leave open, and the §6.2 items include revisit triggers ("*Revisit if PC uptime proves unreliable.*"). The 7 inline `[ASSUMPTION]` tags are the right ones: each is a sensible default, most are configurable, and all are indexed.

**Open items:** 7 assumptions, 7 Open Questions and 1 `[NOTE FOR PM]` make 15 open items on a business-critical PRD that is about to go to build. That would normally be a warning sign, but it doesn't block anything here. Most are setup tasks or configurable defaults, and the only one that affects architecture (OQ-5, the Google Places terms) is already assigned to the architecture step.

The real gaps are elsewhere: inputs from the brief were dropped without comment, and a compliance list looks complete but isn't.

### Findings
- **medium** Inputs from the brief were dropped without comment (FR-5, FR-25, §4.2, §6.2; brief addendum C).
  - The brief lists the SRA register (the solicitors' regulator) as the check for UK law firms, and Google Places review fields as the source of the law Segment's "unreturned calls" Signal.
  - The PRD has no SRA requirement, yet FR-25's example ("lead with the SRA notice") still assumes one.
  - FR-5's assumption that only the place ID is stored rules out review-based Signals: a review excerpt can't serve as Evidence if it can't be stored.
  - Neither cut appears in §6.2.

  *Fix:* list both in §6.2 with the reason (the SRA API is unconfirmed; the Places storage terms forbid it), or bring them back with a requirement.
- **medium** §5.1's CAN-SPAM list leaves out labeling the email as an advertisement (§5.1, FR-23, §4.12). The list reads "truthful From and subject, a postal address, a working opt-out, and opt-outs honored immediately". The FTC's compliance guide also requires commercial email to identify itself clearly as an advertisement or solicitation, though senders have leeway in how they do it. Leaving it out silently looks like a decision nobody made, in a section that says its rules "can't be turned off". *Fix:* state the position, either a footer phrase in FR-23 or a recorded legal view that the rule doesn't apply. Confirm with counsel.
- **medium** The only required backup is on the same PC as the Suppression List it protects (§5.3, FR-50, FR-51). §5.3 backs the data up "daily to a second location on the PC", and the off-PC copy is optional. A disk failure, theft or ransomware would lose:
  - the Suppression List, whose "Entries never expire";
  - the opt-out history;
  - the Provenance Records.

  The engine would then email people who had opted out. *Fix:* make an off-PC copy mandatory for at least the Suppression List and Provenance Records, and add a restore check, for example a monthly test restore shown on the FR-58 health panel.

## Downstream usability — strong
This PRD is at the top of the planning chain (architecture, then epics and stories), so extraction matters. It holds up:
- **Glossary:** about 30 terms, used consistently and capitalized in the FRs. "Operator" and "Mac", and "Sender" and "Furqaan", are kept separate outside the user journeys.
- **IDs:** FR-1 to FR-58, UJ-1 to UJ-5, SM-1 to SM-9 and SM-C1 to SM-C4 run without gaps and have no duplicates.
- **Cross-references:** all resolve, including "brief addendum F", which exists as "F. Open decisions".
- **Traceability:** each feature block names the user journeys it realizes, and each success metric names the FRs it validates.
- **Standalone sections:** most sections make sense on their own.

### Findings
- **medium** The Signal catalog, which FR-11 has to detect, exists only in the brief (FR-1, FR-11, §0). FR-1 ships the two Segments with "Signal types with weights" but lists neither the types nor the weights; the catalog is in brief addendum C. §0 says the PRD doesn't repeat the brief's *why*, but the Signal list is a requirement, not reasoning. A story writer extracting FR-11 from the PRD won't find what to detect. *Fix:* add a table under FR-1 with each Signal, its Segment, source, Evidence type (see the Evidence finding under Done-ness) and default weight.
- **low** It isn't clear what counts against the Daily Cap (Glossary "Daily Cap", FR-34, FR-43). The Daily Cap counts "cold emails", and FR-34 says "No inbox exceeds its Daily Cap". Neither says whether replies to Hot Leads count, or whether a full cap or the 4–12 minute gap can delay them. The scheduler story needs to know. *Fix:* state whether Reply Drafts count against the Daily Cap and whether they wait for the random gap and the Send Window.

## Shape fit — strong
The shape fits the product. This is a single-Operator internal tool, written as a capability spec: 14 feature blocks and 58 FRs. The five user journeys are short and work as end-to-end acceptance scenarios, not as persona filler, and each carries a rule or an edge case: UJ-1's red card that can't be approved, UJ-5's catch-up without a burst. The metrics are operational (SM-6 Operator time, SM-9 running cost) as well as business outcomes, which suits an internal tool.

**On length:** 5,892 words is above the "~5–8 pages" agreed at kickoff (`.memlog.md`), but the length is justified:
- 56% of it (3,269 words) is §4, and nearly every FR there has a number, a rule or a default.
- The Glossary (622 words) is used throughout.

The answer is to cut repetition, not to move requirements into the addendum. The addendum calls itself "recommendations and constraints, not decisions", so moving FRs there would weaken them.

### Findings
- **low** About 500 words of repetition can be cut (§0, §2.2, UJ-4, UJ-5, §5.2, §5.5, FR-56, the §4 feature descriptions).
  - §2.2 Non-Users repeats §7 and §6.2 ("Not for clients" says the same as "Not a product for other companies in v1").
  - UJ-4 and UJ-5 restate FR-41/FR-37 and FR-36, and can shrink to one line each.
  - §5.5 restates FR-32, FR-38 and SM-5, and §5.2 restates SM-9 and FR-57.
  - FR-56's list of credentials duplicates addendum §2 and can move there.
  - §0 can shrink to two lines.
  - Several feature descriptions (§4.4, §4.5, §4.14) only preview their FRs.

  Move one thing the other way: the 300 ms limit (addendum §3) belongs in FR-29. The additions this review asks for (the first-send slice, the Signal catalog, Company Facts and the Evidence types) will take about as many words as these cuts save. So the target is the same length with more in it, not a shorter document. *Fix:* make these cuts while making the high-severity changes.

## Mechanical notes
- **ID continuity:** FR-1 to FR-58, UJ-1 to UJ-5, SM-1 to SM-9 and SM-C1 to SM-C4 run without gaps and have no duplicates. §0's "FR-1 to FR-58" matches.
- **Assumptions Index round trip:**
  - All 7 inline tags are indexed.
  - Index entry 8 ("Brief: the Offer ladder… is to be validated on the first 10 calls") has no inline tag. Tag the Glossary entry "Offer".
  - The tag format varies: FR-15 and FR-17 use a backticked `` `[ASSUMPTION]` `` prefix, and the rest use an inline `[ASSUMPTION: …]`. Make them consistent so tools can extract them.
- **Glossary drift and terms used for two things:**
  - "Interested" is both a Reply Class and a Pipeline Stage (FR-45). Hot Leads classed *Question* or *Referral* never reach the *Interested* stage, so the FR-54 "Hot Leads" count and the stage counts will differ.
  - "Bounce" is a Reply Class, but bounces are delivery-failure notices handled by FR-44, not Replies on a thread.
  - "No Contact" (FR-13) is a Lead status that appears in neither the Glossary nor the Pipeline Stages.
  - "approved company facts" (FR-43) is undefined (see Done-ness).
  - FR-45 says "deal value" and FR-54 says "won value".
  - FR-51 records "the Segment/Market rule that made it Eligible", but §5.1 says FR-51 records the "legitimate-interest basis… per Contact". Align the wording so the legitimate-interest basis is visibly recorded.
- **Internal tensions:**
  - FR-43 puts Reply Drafts at "the top of the Approval Queue", but FR-26 has items "grouped by type and sorted by Lead Score". Say that the Reply Draft group sorts first.
  - OQ-7 overlaps FR-30 (see Decision-readiness).
  - §5.4's "All data stays on the local PC" conflicts with §5.3's optional cloud-folder backup.
- **User journey protagonists:** UJ-1, UJ-2 and UJ-3 have named protagonists (Mac, Furqaan). UJ-4 ("A recipient") and UJ-5 (the PC) describe system behavior with no protagonist, which supports turning them into acceptance lines under FR-41 and FR-36.
- **Required sections:** all are present for an internal PRD at the top of the planning chain: Vision, users, Glossary, FRs, cross-cutting requirements, scope, Non-Goals, success metrics with counter-metrics, Open Questions and the Assumptions Index.

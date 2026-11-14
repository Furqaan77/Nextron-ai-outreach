---
title: "Research Reconciliation: Nextron Outreach Engine PRD"
created: 2026-11-14
checked:
  - prd.md
  - addendum.md
against:
  - docs/research/outreach-landscape-2026.md
  - docs/research/icp-segments-2026.md
  - docs/research/nextron-company-profile.md
---

# Research Reconciliation: Nextron Outreach Engine PRD

This file checks `prd.md` and `addendum.md` against the three research files. The brief and its addendum were also read, because the PRD defers to them for market rules. Line numbers (L#) refer to the files as they stood on 2026-11-14. File short names:
- **LAND** = `docs/research/outreach-landscape-2026.md`
- **ICP** = `docs/research/icp-segments-2026.md`
- **CO** = `docs/research/nextron-company-profile.md`
- **PRD** = `prd.md`
- **ADD** = `addendum.md` (PRD addendum)
- **B / BADD** = brief / brief addendum

**Severity:**
- **High:** legal exposure, damage to a domain's reputation, or a plan that can't work as written.
- **Med:** a rule left unenforced, or lower lead quality.
- **Low:** hygiene or wording.

**Category:**
1. Legal, platform or deliverability constraint not enforced, or wrong.
2. Segment signal or data source missing.
3. PRD claim that contradicts the research.
4. Company proof point not used.

## Summary

| ID | Sev | Cat | Finding | PRD location |
|---|---|---|---|---|
| R-01 | High | 3, 1 | The 30% cap on *Likely* addresses can't be met with the free verification credits in the research. Throughput falls to about 10–13 new Contacts a day, not 20–25 | FR-15, FR-19, ADD §4 |
| R-02 | High | 1 | No SPF/DKIM/DMARC check before an inbox may send. Subdomains of nextronai.com aren't blocked. No limit on inboxes per domain | FR-32, FR-58 |
| R-03 | High | 1, 3 | One-click unsubscribe (RFC 8058) is dropped. The research lists it as a Yahoo rule with no threshold, and the brief puts it in v1. A mailto header doesn't meet the rule | FR-41, §6.2, §10 |
| R-04 | High | 1, 3 | WhatsApp and Instagram rules are weaker than the research. "Shared a number" isn't opt-in, and FR-47 allows drafts for any Hot Lead, including leads who replied only by email | FR-24, FR-47 |
| R-05 | High | 1 | Cold sends can start on day 1 of a new inbox. No warm-up period is enforced, and sends by a warm-up tool aren't counted | FR-33 |
| R-06 | High | 2 | No default list of Signals per Segment. Dynamics GP, legal tech stack, review complaints, news and acquisitions, and practice area are all left unspecified. No negative Signals | FR-1, FR-7, FR-10, FR-11, FR-17 |
| R-07 | High | 4 | Drafting has no case-study or proof library. Legal AI and Wish Wheels are never mentioned, and "approved company facts" is never defined | FR-4, FR-20, FR-21, FR-43 |
| R-08 | Med | 1 | Bounces aren't classified. Auth or policy rejections (5.7.x) would mark good addresses Invalid, and temporary 4xx rejections count as bounces | FR-16, FR-38, FR-44 |
| R-09 | Med | 1 | The spam-complaint pause has no data source. The 0.3% hard limit isn't modelled | FR-38, §5.5, SM-5 |
| R-10 | Med | 1, 3 | The Places numbers don't add up. Website URL is an Enterprise field, so "Pro-only field masks" can't serve FR-5, and 1,000–1,500 candidates a month exceeds 1,000 free calls. Scraping the Maps website isn't banned | FR-5, FR-57, ADD §2, §4 |
| R-11 | Med | 2 | The "reviews about unreturned calls" signal is dropped. Storing review text clashes with the place-ID-only storage assumption | FR-5, FR-11, OQ-5 |
| R-12 | Med | 2 | Absence signals (no chat, no booking) can't meet the verbatim-excerpt Evidence rule. A plain HTTP crawler misreads sites rendered in JavaScript; Nextron's own site is an example | FR-10, FR-11, FR-21 |
| R-13 | Med | 1 | The Market check looks only at the Lead's country. Contacts in Canadian, German, EU or Gulf offices, or on those country domains, get through | FR-2, FR-18 |
| R-14 | Med | 1, 3 | The Market and Segment settings can't express rules the research names: CASL conditions, consent-only markets, rules with start dates, and blackout months. That contradicts §6.2's claim that new ones need no code changes | FR-1, FR-2, §6.2 |
| R-15 | Med | 1 | CAN-SPAM gaps: subject lines aren't checked although §5.1 claims they are; sending isn't blocked while the postal address is blank; opt-outs from colleagues or inside out-of-office replies are missed | FR-21, FR-23, FR-37, FR-41, §5.1 |
| R-16 | Med | 3 | One Offer ladder for both Segments, named "Private AI". The research gives each Segment a different entry offer, at higher prices | Glossary "Offer", FR-3 |
| R-17 | Med | 2, 3 | No checked list of regulatory and market facts (ABA 512, SRA notice, Dynamics GP dates). The grounding check covers only claims about the Lead | FR-21, FR-25, FR-43 |
| R-18 | Med | 2 | No data source for company size or revenue in the Ops-Automation Fit Score. Companies House accounts and SIC data and EDGAR go unused. The CFO isn't a known role | FR-1, FR-6, FR-10, FR-13, FR-17 |
| R-19 | Med | 2 | Greenhouse, Lever and Ashby serve one company's board at a time, so they're a signal source, not a discovery source. Coverage in these Segments is probably low | FR-7, §4.2 |
| R-20 | Med | 1 | UK GDPR gaps: Contacts who are never emailed never reach the retention clock; every name on a team page is stored; there's no privacy notice; UK sends don't require an LIA | FR-10, FR-51, FR-53, §5.1, OQ-6 |
| R-21 | Med | 1 | The UK legal-form check relies on matching the Lead to a Companies House record, but the matching rule isn't defined. Free-webmail addresses at UK firms are allowed. The SRA register is missing | FR-6, FR-12, FR-18 |
| R-22 | Low | 1 | The LinkedIn default (20 a day) sits exactly at the unverified 100-a-week ceiling. The PRD never says whose account sends | FR-48, UJ-3 |
| R-23 | Low | 4 | Gaps in the company profile (no metrics, no social links, a site crawlers can't read) aren't carried into the PRD as launch prerequisites | §9, §6 |
| R-24 | Low | 3 | The cost model assumes Workspace on annual billing ($7). Monthly billing is $8.40, and the research marks the figure ⚠ | §5.2 |

---

## Category 1: Legal, platform and deliverability constraints

### R-01 (High, Cat 3 and 1): the *Likely* cap can't be met with free verification credits
- **Source:**
  - LAND L23: Hunter gives 50 credits a month; a find costs 1 credit and a verification 0.5. Domain Search returns at most 10 results. Starter costs $34/mo billed annually, for 2,000 credits.
  - LAND L24: Prospeo gives 100 credits a month, and its free-tier API access is "Limited".
- **PRD location:**
  - FR-15 L179: *Likely* addresses are capped at 30% of daily sends.
  - FR-19 L203: about 20–25 new Contacts a day.
  - FR-14 L166: Hunter is used first as a finder.
  - ADD §4 L40 and L42: "about 22 new Contacts a day"; "150 verification credits a month combined"; "most addresses will be *Likely*… This is why FR-15 caps *Likely* sends."
- **Gap:**
  - **Need:** about 22 new Contacts a weekday is about 475 a month. With a 30% cap on *Likely* sends, about 70% of Contacts (about 335 a month) must be *Verified*.
  - **Supply:** the research's free tiers give at most about 100 Hunter verifications (50 credits ÷ 0.5) and up to 100 Prospeo credits. Each Hunter find (1 credit, FR-14 step 3) uses up the same pool, and results that come back invalid or unknown also use credits. Realistic supply is about 150 a month.
  - **Result:** under the cap that supports about 215 Contacts a month, or 10–13 a day. The Sending Inboxes would run at about 45–60% of their 90-a-day capacity.
  - **Internal contradictions:**
    - The addendum says "most addresses will be *Likely*", yet the cap limits *Likely* to 30%.
    - The addendum counts credits as if they were verifications.
    - FR-14 and FR-56 rely on the Prospeo API, which the research marks as "Limited" on the free tier.
- **Suggested fix:** choose one of these and state it in FR-15 and §10:
  1. Split *Likely* into *Published* (printed on the Lead's own site, so low bounce risk) and *Inferred* (pattern-derived, unverified). Apply the cap only to *Inferred*.
  2. Budget for Hunter Starter ($34/mo annual), which fits the brief's headroom (BADD §E).
  3. Lower FR-19's assumption to about 10–13 a day and resize the inboxes.

  Then:
  - In FR-57, meter credits per operation (find = 1, verify = 0.5).
  - Spend verifications only on *Inferred* addresses.
  - Call Hunter Domain Search once per domain to learn the address pattern, rather than paying for a find per person.
  - Tag Prospeo API access `[ASSUMPTION]` until confirmed.

### R-02 (High, Cat 1): no email-authentication check before sending
- **Source:**
  - LAND L7: every sender needs SPF or DKIM, a valid PTR record, TLS, and a spam rate under 0.3%.
  - LAND L8: Gmail requires SPF, DKIM and DMARC aligned with the From domain.
  - LAND L10: Yahoo requires SPF, DKIM and an aligned DMARC record.
  - LAND L11: Outlook.com sends non-compliant mail to Junk and plans to reject it with error 550 5.7.515.
  - LAND L15: about 3 inboxes per domain.
  - LAND L16: separate lookalike domains; never nextronai.com.
- **PRD location:**
  - FR-32 L267–268 refuses only an inbox "on `nextronai.com`".
  - FR-58 L386–390: the health panel shows no DNS status.
  - §5.5 L416–418.
  - DNS setup appears only as a manual checklist in BADD §A step 4.
- **Gap:**
  - Nothing in the engine checks the Sending Domain's SPF, DKIM and DMARC records, or their alignment, before cold sends start.
  - Nothing notices if those records break later.
  - An inbox on a subdomain such as `mail.nextronai.com` passes FR-32's literal check, but shares nextronai.com's domain reputation under DMARC.
  - There's no limit on inboxes per Sending Domain.
- **Suggested fix:** add a DNS preflight to FR-32 that runs on connect and then daily:
  - SPF includes the mail provider.
  - The DKIM selector is published, 2048-bit.
  - A DMARC record exists and the From domain is aligned.

  If the check fails, block cold sends from that domain and show the failure in FR-58. Also:
  - Refuse `nextronai.com` and every subdomain of it.
  - Warn when a Sending Domain has more than 3 Sending Inboxes.
  - State that all mail is relayed through the mailbox provider, never sent directly from the PC to the recipient's mail server. Relaying is what meets the PTR and TLS rules.

### R-03 (High, Cat 1 and 3): one-click unsubscribe dropped
- **Source:**
  - LAND L10: Yahoo requires "RFC 8058 one-click unsubscribe"; the research gives no volume threshold.
  - LAND L8: Gmail requires one-click unsubscribe at 5,000+ a day.
  - B L97 and BADD L28 put one-click unsubscribe in v1 scope.
- **PRD location:**
  - FR-41 L311: a mailto `List-Unsubscribe` header only.
  - §6.2 L434: "irrelevant at v1 volumes… revisit before any inbox approaches 5,000 sends a day".
  - §10 L485.
  - ADD §3 L35.
- **Gap:**
  - A mailto header doesn't meet RFC 8058, which needs an HTTPS URL and a `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header.
  - The PRD quietly narrows both the research and the brief's scope.
  - The revisit trigger uses the wrong unit. The 5,000-a-day figure is per sender, which providers count across the whole sending domain, not per inbox. An inbox capped at 30 a day can never approach it, so the trigger can never fire.
- **Suggested fix:** choose one:
  1. Add RFC 8058 one-click unsubscribe through a minimal hosted endpoint (a free serverless function or form backend). It records a signed token, and the engine polls it and turns each request into a Suppression List entry. The dashboard stays localhost-only.
  2. Keep mailto, but record this as a decision that changes the brief, with a source showing that Yahoo's one-click rule doesn't apply at about 90 sends a day. Update the brief's scope line, and correct LAND L10 if the research overstates the rule.

  In both cases, restate the trigger as total daily sends per Sending Domain.

### R-04 (High, Cat 1 and 3): WhatsApp and Instagram rules weaker than the research
- **Source:**
  - LAND L36: on Instagram, a business may message only after the user acts first. There's a 24-hour window, and one private reply to a comment within 7 days.
  - LAND L37: on WhatsApp, a business may message only people who gave their number *and* opted in. Templates are required outside the 24-hour window (policy updated Sep 2026).
  - BADD L31: "only numbers the prospect shared and opted in".
- **PRD location:**
  - FR-24 L227: "used only when the prospect has engaged on that channel or shared a number".
  - FR-47 L335: for any Hot Lead, the Operator can ask for a drafted LinkedIn, WhatsApp or Instagram message, with no condition.
  - §5.1 L399.
- **Gap:**
  1. Sharing a number isn't opting in. A number in an email signature, or one found on the Lead's website (FR-10 extracts phone numbers, and FR-47 call sheets carry them), would qualify under FR-24's wording.
  2. FR-47 sets no channel condition. A Hot Lead is defined by an email reply, which isn't engagement on Instagram or WhatsApp. FR-47 is looser than FR-24.
  3. The engine stores no record that the prospect acted first on the channel, so it can't show the 24-hour window or the 7-day window for replying to a comment.
- **Suggested fix:**
  - Add a Channel Permission record per Contact: the channel, what the prospect did (for example "sent us a DM" or "asked to move to WhatsApp"), the date and the Evidence.
  - Create WhatsApp and Instagram Drafts in FR-24 and FR-47 only when that record exists, and show when the window closes.
  - Reword FR-24 to "shared their number and agreed to be contacted on WhatsApp".
  - Note in §6.2 that WhatsApp Business API sending, if added later, needs approved templates outside the 24-hour window.

### R-05 (High, Cat 1): cold sends can start before warm-up
- **Source:**
  - LAND L14: warm up for 2–4 weeks, starting at 5–10 a day.
  - LAND L13: 25–50 sends per inbox per day.
- **PRD location:**
  - FR-33 L271–272: the ramp starts at 5 a day, rises by 5 a week to 30; "Warm-up activity itself… is outside v1. The engine only enforces the ramp."
  - §6.2 L431.
  - OQ-1.
- **Gap:**
  - A new inbox can send cold email on day 1. The ramp starts cold sending at 5 a day straight away, while the research describes 2–4 weeks of warm-up before cold volume.
  - If a paid warm-up tool runs on the same inbox, its sends don't count toward the inbox's daily total, so combined volume can exceed safe levels.
- **Suggested fix:**
  - Add a "warm-up started" date to each Sending Inbox (FR-32), and a minimum warm-up period (default 14 days, editable) before the first cold send. The FR-33 ramp starts after that.
  - Let the Operator enter the warm-up tool's daily volume, or count it from the Sent folder, and enforce a total daily ceiling per inbox.

### R-08 (Med, Cat 1): bounces aren't classified
- **Source:**
  - LAND L9: Gmail tightened enforcement in Nov 2025, with temporary and permanent rejections.
  - LAND L11: Outlook sends to Junk, then rejects with 550 5.7.515.
- **PRD location:**
  - FR-16 L182: any bounce marks the address Invalid.
  - FR-38 L296: pause when the 7-day bounce rate exceeds 3%.
  - FR-44 L323.
- **Gap:**
  - An authentication or policy rejection (5.7.x, such as 5.7.515) is a problem with the sender, not the address. The engine would throw away a good Contact.
  - Worse, it would treat a domain-wide authentication failure as a few bad addresses instead of stopping the inbox at once.
  - Temporary 4xx rejections should be retried, not counted as bounces.
- **Suggested fix:** classify bounces in FR-44 into three kinds:

  | Kind | Codes | Action |
  |---|---|---|
  | Hard (bad address) | 5.1.x | Feeds FR-16 and the bounce rate |
  | Temporary | 4.x.x | Retry inside the Send Window; treat as hard after N tries |
  | Policy or authentication | 5.7.x | Pause the inbox and alert at once (FR-38); leave the address status unchanged |

### R-09 (Med, Cat 1): the spam-complaint trigger has no data source
- **Source:**
  - LAND L7: spam rate under 0.3%, target under 0.1%.
  - LAND L10: Yahoo complaint rate under 0.3%.
- **PRD location:**
  - FR-38 L296: "any spam complaint or blocklist signal is detected".
  - §5.5 L418.
  - SM-5 L457.
- **Gap:**
  - The PRD names no source for spam complaints or blocklistings.
  - Gmail doesn't tell the sender about individual complaints. Postmaster Tools reports only a spam rate for the whole domain, and may show nothing at low volume.
  - As written, FR-38's complaint trigger can't fire, and SM-5 can't be measured.
- **Suggested fix:** name the sources in FR-38:
  - Google Postmaster Tools for each Sending Domain. Record "no data" rather than "0%" when it reports nothing.
  - Scheduled DNS blocklist lookups for each Sending Domain.
  - Replies classified as hostile or "spam" accusations, as a stand-in measure.

  Pause on a blocklisting or at the research's 0.3% hard limit, and alert at 0.1%.

### R-10 (Med, Cat 1 and 3): the Places tier numbers don't add up
- **Source:**
  - LAND L22: Text Search Pro (name and address) gives 5,000 a month free. Enterprise fields (website, phone, rating) give 1,000 a month free, then cost $35 per 1,000.
  - LAND L29: OpenStreetMap Overpass is for light use only; commercial users should self-host.
  - LAND L55: the Maps-website scraper's Terms-of-Service risk is unverified.
- **PRD location:**
  - FR-5 L121–123: records place ID, name and website URL; stops at "the configured quota".
  - FR-57 L381–383.
  - ADD §2 L22: field masks keep usage in the free tiers.
  - ADD §4 L41: "1,000–1,500 a month… within the free tiers: 1,000 Places Enterprise calls a month, or more with Pro-only field masks."
- **Gap:**
  - Website URL is an Enterprise field (LAND L22), so every FR-5 search is billed as Enterprise. Pro-only field masks can't deliver FR-5.
  - If each candidate costs one Enterprise call, 1,000–1,500 candidates a month exceeds the 1,000 free calls. That means either about $17.50 a month in overage, or discovery stopping around day 20 of the month.
  - The PRD never says whether billing is per request or per place.
  - FR-5 contradicts itself: it "records" name and website, but assumes only the place ID may be kept.
  - Scraping the Maps website isn't explicitly banned; §4.2 bans scraping LinkedIn and Instagram only.
- **Suggested fix:**
  - State the billing unit. Text Search bills per request, and one request returns a page of several places; confirm the page size during architecture.
  - In FR-57, set separate quotas for Pro (5,000) and Enterprise (1,000), with a hard stop before any paid usage.
  - Either get the website from an Enterprise Text Search (counted per request), or run a Pro search and find the website another way.
  - Mark the name and website from Places as temporary until facts from the Lead's own site replace them.
  - Add "never scrapes the Google Maps website" to §4.2 and FR-49.
  - List a self-hosted OpenStreetMap copy as an optional backup source, not the public Overpass endpoint.

### R-13 (Med, Cat 1): Market check uses the Lead's country only
- **Source:**
  - LAND L42: Germany requires consent even for B2B; elsewhere in the EU it depends on the country.
  - LAND L43: Canada requires consent, with a narrow exception, and bans address-harvesting software.
  - LAND L44: the UAE and Saudi Arabia require consent.
  - LAND L46: the implication for which markets can be cold-emailed.
- **PRD location:**
  - FR-2 L107: "Any Lead whose country is not an enabled Market is blocked."
  - FR-18 L193.
  - §5.1 L398.
- **Gap:**
  - A US law firm with a Toronto office, or a UK distributor whose operations director works in Germany, passes FR-2 and FR-18.
  - The PRD never says how a Lead's country is decided: from the Places address, the website or Companies House.
  - FR-10 harvests published addresses from websites, which is the activity CASL bans. A Canadian Contact slipping through is the worst case.
- **Suggested fix:** define the country rule in FR-2 and apply it to each Contact:
  - The Contact's office location (from the team page) must be in an enabled Market.
  - The email domain's country code (.ca, .de, .ae, .sa and so on) must also be in an enabled Market.
  - If the location is unknown, fall back to the Lead's country and flag it.
  - Show the deciding fact in the Provenance Record (FR-51).

### R-14 (Med, Cat 1 and 3): the settings can't express the research's market rules
- **Source:**
  - LAND L41–45: the rules differ by market:
    - UK: the corporate-subscriber test.
    - EU: a legitimate-interest assessment, country by country.
    - Germany and the Gulf: consent required.
    - Canada: a narrow exception with three conditions.
    - India: DPDP consent duties from about May 2027.
  - ICP L40: CPA firms should not be contacted from January to April.
- **PRD location:**
  - FR-1 L99.
  - FR-2 L104: a Market's rules are only whether cold email is allowed, eligible legal forms, timezones and footer.
  - §6.2 L436: "The configuration model (FR-1, FR-2) must allow adding [Gulf, India, EU, Canada; CPA, RIA, clinics, real estate] without code changes."
- **Gap:** FR-2's fields can't express:
  - Canada's three conditions: the recipient published the address themselves, it carries no "no solicitation" notice, and the message is relevant to their role.
  - A consent-only Market (Gulf, Germany) that sends work to LinkedIn and warm channels instead of blocking it outright.
  - A rule that changes on a date (India).
  - A required legitimate-interest assessment per country.

  FR-1 has no blackout months for a Segment. So §6.2's "no code changes" claim isn't true of the model as specified.
- **Suggested fix:** either extend FR-1 and FR-2 now, or soften §6.2 to say that some new Markets will need code. An extended model would add:
  - a cold-email mode per Market: allowed, allowed with conditions, consent only (route to warm channels), or blocked;
  - conditions on where an address came from;
  - a check that the message is relevant to the recipient's role;
  - a required legitimate-interest assessment reference;
  - start dates for rules;
  - blackout periods per Segment.

### R-15 (Med, Cat 1): CAN-SPAM gaps
- **Source:**
  - LAND L40: CAN-SPAM requires truthful headers and subject, a postal address, and opt-outs honoured within 10 business days. Fines reach $53,088 per email.
  - LAND L41: PECR requires identifying yourself and offering an opt-out.
- **PRD location:**
  - §5.1 L395: maps "truthful From and subject" to FR-23, FR-41 and FR-50.
  - FR-21 L216.
  - FR-23 L223–224.
  - FR-37 L287–293.
  - FR-41 L310.
  - OQ-2 L472.
- **Gap:**
  1. None of FR-23, FR-41 or FR-50 checks the subject line, and FR-21 checks only the body. A misleading Step 1 subject, such as a fake "Re:" or "Fwd:" or a subject unrelated to the body, would pass.
  2. Nothing blocks sending while the postal address or legal name is still blank, and OQ-2 is still open.
  3. Some opt-outs are missed:
     - a colleague or assistant replies "remove us";
     - a reply is both an out-of-office and an opt-out (FR-37 doesn't stop on out-of-office);
     - the classification is uncertain.
- **Suggested fix:**
  - Extend FR-21 to subject lines: no "Re:" or "Fwd:" on Step 1, and the subject must relate to the body.
  - Add a send preflight: nothing is sent until the legal name and postal address are set.
  - In FR-41:
    - any Reply on the thread from the Lead's domain stops the Sequence;
    - "remove us" or "remove our firm" adds the whole domain to the Suppression List;
    - opt-out wording overrides an out-of-office class;
    - an uncertain classification stops the Sequence until the Operator decides.

### R-20 (Med, Cat 1): UK GDPR gaps
- **Source:**
  - LAND L41: "UK GDPR covers named contacts."
  - LAND L42: legitimate interest needs a balancing test.
- **PRD location:**
  - FR-10 L145: extracts "named people with roles".
  - FR-51 L356.
  - FR-53 L362: deletion 12 months "after the last Step".
  - §5.1 L396–397.
  - OQ-6 L476.
- **Gap:**
  1. The retention clock starts at the last Step. Contacts who were discovered but never emailed are never deleted, and they are most of them, because discovery finds 2–3× more than needed.
  2. FR-10 stores every named person on a team page, not just possible decision-makers.
  3. Named UK contacts get no notice of where their data came from or how to object. This follows from UK GDPR's transparency duty; it is not stated in the research, so confirm it.
  4. The legitimate-interests assessment (LIA) is listed as a business prerequisite, but the engine doesn't stop UK sends when one is missing.
- **Suggested fix:**
  - Start the retention clock at discovery for Contacts who are never emailed (for example, 6 months).
  - Keep only people who match the Segment's roles, and drop the rest after selection.
  - Put a privacy-notice link in the signature; FR-4 allows links there.
  - Block activation of a UK Campaign until an LIA reference and date are recorded on it.

### R-21 (Med, Cat 1): UK legal-form check depends on an undefined match
- **Source:**
  - LAND L41: sole traders and some partnerships need consent.
  - LAND L27: Companies House.
  - BADD L51: the SRA register as the UK law-firm check (⚠ API availability unconfirmed).
- **PRD location:**
  - FR-6 L126.
  - FR-12 L153.
  - FR-18 L194.
- **Gap:**
  - FR-12 depends on matching the Lead (found by trading name and website) to the right Companies House entry, but the PRD gives no matching rule. A loose name match could give an unincorporated partnership the legal form of a similarly named Ltd.
  - A Contact on a free-webmail address (such as gmail.com) at a UK firm is probably an individual subscriber, even when the firm is a company.
  - The SRA register, named in the brief addendum, is missing from the PRD.
- **Suggested fix:**
  - In FR-12, treat the legal form as confirmed only when Evidence links the Lead to the record: either the company number shown on the Lead's site, or the registered name and address matching the site. Anything weaker counts as "can't be confirmed", so the Lead is ineligible.
  - Make free-webmail addresses ineligible for UK cold email.
  - Add the SRA register as an optional check once its API is confirmed.

### R-22 (Low, Cat 1): LinkedIn margin and sending account
- **Source:** LAND L35: LinkedIn User Agreement §8.2. Vendors report about 100 invites a week as safe, but this is unverified (⚠).
- **PRD location:**
  - FR-48 L341: default 20 a day, at most 100 per rolling week.
  - UJ-3 L55: "for Mac to send by hand".
- **Gap:**
  - 20 a day over five weekdays hits the unverified ceiling exactly, with no margin.
  - The PRD never says whose LinkedIn account sends the Tasks. If Mac sends from Furqaan's account, that is account sharing, which raises a further User Agreement question not covered by the research; confirm it.
- **Suggested fix:**
  - Set the default to 15 a day (75 a week) and keep 100 a week as the hard ceiling.
  - Lower the default if the acceptance rate falls.
  - State whose account sends. If it is Furqaan's, Furqaan presses send, and Mac's role is to prepare the queue.

---

## Category 2: Segment signals and data sources

### R-06 (High, Cat 2): no default Signal list for the two Segments
- **Source:**
  - ICP L20–24, law firms:
    - job ads for intake, legal operations, knowledge management or innovation roles;
    - Clio, iManage or NetDocuments in job ads;
    - a contact form only, with no chat, booking or after-hours line;
    - reviews about unreturned calls.
  - ICP L26: custom work wins where data must stay on-premises or in-region, or the firm needs integration with its document system.
  - ICP L57–60, ops companies:
    - job posts for data entry, AP clerks, dispatchers or spreadsheet-heavy coordinators;
    - Microsoft Dynamics GP (no new licences since April 2025; support ends 31 December 2029);
    - news of new facilities or acquisitions.
  - ICP L27 and L62: red flags.
  - LAND L50: top senders use signals such as hiring, funding or launches.
  - BADD §C L57–63 holds the full table.
- **PRD location:**
  - FR-1 L99–101: the shipped Segments have size ranges and roles, but no default Signals or weights.
  - FR-7 L130: generic "Segment Signal keywords".
  - FR-10 L143–145: the crawl list has no news, press or blog pages, and "technology mentions" isn't defined.
  - FR-11 L148.
  - FR-17 L188–189.
- **Gap:**
  - The PRD never lists the Signals the two shipped Segments must detect. Architecture and stories could build the general machinery without the specific detectors that decide lead quality.
  - Missing in particular:
    - Dynamics GP, which has a fixed deadline and is the strongest Ops signal;
    - legal tech systems named in job ads;
    - an after-hours line;
    - practice area (document-heavy practices);
    - facility, acquisition or funding news, for which there is no source at all, because FR-10 doesn't crawl news or press pages.
  - There are no negative Signals, even though the research lists red flags. Examples: the site already advertises Harvey, CoCounsel or an in-house AI product; the firm has more than 50 lawyers.
- **Suggested fix:**
  - Add a default Signal list to FR-1, or make BADD §C binding. Each entry gives the Signal, its Segment, its source (careers page, job board, website, Places or Signal Inbox), its keywords, its weight and how fast it ages.
  - Add news, press, blog and insights pages to FR-10's crawl list.
  - Add negative Signals and disqualifiers to FR-17.
  - Name Dynamics GP as its own detector across job ads and website text.

### R-11 (Med, Cat 2): the review-complaint signal is dropped
- **Source:**
  - ICP L24: "Reviews complaining about unreturned calls."
  - LAND L22: rating is an Enterprise field.
  - BADD L55: Places review fields are the planned source.
- **PRD location:**
  - FR-5 L121–122: only place ID, name and website.
  - FR-11.
  - OQ-5 L475.
  - §10 L481.
- **Gap:**
  - No FR fetches reviews or ratings, so the law Segment loses one of its four research signals.
  - If reviews are added, FR-11 requires storing a verbatim excerpt as Evidence. That clashes with the assumption that only the place ID may be stored.
- **Suggested fix:** decide in FR-5 and FR-11. Either:
  - fetch rating and review text (a higher-cost tier, counted against the quota), use them for scoring only, and store a link plus a short note for the Operator, within the terms OQ-5 confirms; or
  - drop the signal explicitly, here and in the brief's list.

  In both cases, the drafting rules should forbid quoting a negative review back to a prospect.

### R-12 (Med, Cat 2): absence signals and JavaScript-rendered sites
- **Source:**
  - ICP L23: this signal is defined by what's missing: "only a contact form: no chat, no booking, no after-hours line".
  - CO L3 and L51: Nextron's own site renders in the browser; the raw HTML is a 61-character shell.
- **PRD location:**
  - FR-10 L143–145: the crawl, and extraction of contact methods.
  - FR-11 L148–149: Evidence is a URL plus a verbatim excerpt of up to 300 characters; Signals without Evidence are never stored.
  - FR-21.
- **Gap:**
  - An absence can't be backed by a verbatim excerpt. As written, FR-11 either can't store the research's contact-form-only signal, or stores it with weak Evidence.
  - A plain HTTP crawler:
    - sees nothing on sites that render in the browser (Nextron's own site is one);
    - misses chat and booking widgets that JavaScript adds after the page loads.
  - The result is a false "no chat" Signal that a Draft could then state as fact.
- **Suggested fix:**
  - Add an "observed absence" Evidence type: the pages checked, the detector used and the time.
  - Look for widget scripts in the raw HTML. When the raw HTML is nearly empty, render the page in a headless browser.
  - Use absence Signals for scoring only, or allow only hedged wording ("I couldn't find online booking on your site") and have the grounding check enforce it.

### R-18 (Med, Cat 2): no source for Ops company size or revenue
- **Source:**
  - ICP L50: Ops companies with $10M–$500M revenue.
  - ICP L56: the COO or VP Operations champions the project and the CFO signs; ERP-type purchases take 9–15 months.
  - LAND L27: Companies House.
  - LAND L28: SEC EDGAR is free, allows 10 requests a second, and requires a User-Agent header.
- **PRD location:**
  - FR-1 L100: estimated revenue $10M–$200M; roles COO, VP Operations, owner, CEO.
  - FR-6 L126: name, number, legal form, status, officers.
  - FR-10 L145: "a size estimate (e.g. attorneys listed)".
  - FR-13.
  - FR-17.
- **Gap:**
  - The Fit Score depends on a revenue band, but no FR names a source for revenue or headcount for Ops Leads. The only size example is "attorneys listed".
  - Companies House gives free UK size clues (accounts category, SIC codes, filing history), and FR-6 doesn't retrieve them.
  - EDGAR isn't used. For the few US companies that file, it shows funding and acquisitions.
  - The CFO, who signs, isn't a known role.
  - Narrowing the revenue range from $500M to $200M follows the brief and is fine.
- **Suggested fix:**
  - Add size clues for Ops Leads to FR-10 and FR-6: headcount stated on the site, number of locations or warehouses, fleet size, Companies House accounts category and SIC code.
  - Add an optional EDGAR lookup for funding and acquisition filings (with the required User-Agent and at most 10 requests a second).
  - Store the size estimate with a confidence level and its Evidence.
  - Add the CFO as a secondary role. Record the CFO on the call sheet (FR-47), even though only one Contact per Lead is in a Sequence (FR-19).

### R-19 (Med, Cat 2): job boards are a signal source, not a discovery source
- **Source:**
  - LAND L30: Greenhouse, Lever and Ashby have public endpoints with no key, listed as "hiring signals".
  - ICP L21 and L58: the hiring signals for each Segment.
- **PRD location:**
  - FR-7 L130, placed under §4.2 Lead Discovery.
  - §4.2 description, L118.
- **Gap:**
  - These endpoints return one company's job board at a time, and you need that company's board name. They can't find new Leads by keyword; the research lists them as a signal source.
  - Small law firms and mid-size distributors rarely use these three systems. This is an inference, not stated in the research.
  - So most hiring Signals for these Segments will come from careers pages, which FR-10 crawls but FR-7 treats as secondary.
- **Suggested fix:**
  - Move FR-7 to §4.3 Enrichment. For a known Lead, detect which job-board system its careers page links to, and fetch that board.
  - Make the careers-page crawl the main hiring source.
  - Show in FR-54 how many Signals each source produces, so the Operator can see whether the job boards earn their keep.

---

## Category 3: PRD claims that contradict the research

R-01, R-03, R-04, R-10 and R-14 above also belong here. These two contradictions belong only here:

### R-16 (Med, Cat 3): one Offer for two Segments, priced below the research
- **Source:**
  - ICP L25: law firms: a fixed-fee AI confidentiality and workflow audit mapped to ABA Op. 512 and the SRA notice; benchmark $3.5k–8.5k.
  - ICP L61: ops companies: a $5k–15k process audit, then a fixed-scope sprint.
  - ICP L7: open with a fixed-scope sprint, not an ERP project.
- **PRD location:**
  - Glossary "Offer", L91: one v1 ladder: a $2,500–$3,500 "Private AI & Workflow Audit", then a $10k–$30k build.
  - FR-3 L110: an Offer per Campaign, but no defaults stated.
  - §10 L488.
- **Gap:**
  - The PRD ships one Offer for both Segments.
  - Its name, "Private AI", fits law firms but not ops buyers, for whom the research recommends a process audit leading to a fixed-scope sprint.
  - Its price is below both research benchmarks ($3.5k–8.5k for law; $5k–15k for ops).
  - This is the brief's assumption ("validate on the first 10 calls"), not an error. But the PRD neither flags the gap from the research nor sets a default per Segment.
- **Suggested fix:**
  - Ship two default Offers:
    - Law: "AI Confidentiality & Workflow Audit (ABA 512 / SRA)".
    - Ops: "Operations Process Audit", leading to a fixed-scope automation sprint.
  - Note in §10 that both prices are deliberately below the research benchmarks, to make the first yes easy, and should be checked on the first 10 calls.

### R-17 (Med, Cat 2 and 3): regulatory and market facts aren't checked
- **Source:**
  - ICP L15: ABA Op. 512 requires informed client consent before client data goes into *self-learning* tools.
  - ICP L16: the SRA warning notice of 17 Aug 2026 requires contractual, technical and organisational safeguards.
  - ICP L59: Dynamics GP dates.
  - ICP L14 and L54: survey statistics.
  - ICP L36–38: Reg S-P, WISP and the NAIC bulletin, for later Segments.
- **PRD location:**
  - FR-21 L216: the grounding check covers "claims about the Lead".
  - FR-25 L230: its example is "lead with the SRA notice".
  - FR-43 L318.
- **Gap:**
  - Drafts will cite regulations and statistics; the PRD's own example asks for the SRA notice. But there's no checked, sourced store of these facts, and the grounding check doesn't cover them.
  - Misstating ABA 512 to a managing partner would destroy credibility.
  - The brief already widens 512 to "AI tools" (B L43), which is broader than the research's "self-learning tools".
- **Suggested fix:**
  - Add a list of approved Segment facts, which can share a store with R-07's Proof Library. Each entry gives the exact approved wording, source URL, Market (ABA 512 is US; the SRA notice is UK) and the date it was last checked.
  - Extend FR-21 so regulatory and statistical claims must match an entry.
  - Fill the list from ICP using the research's own wording.

### R-24 (Low, Cat 3): Workspace cost assumes annual billing
- **Source:** LAND L17: Google Workspace Starter costs about $7 per user a month on annual billing and $8.40 on monthly billing. The research marks the figure ⚠.
- **PRD location:** §5.2 L402.
- **Gap:**
  - The $35–50 target assumes a one-year commitment for 3 inboxes, on a domain that may need replacing if its reputation is damaged.
  - Monthly billing adds about $4 a month. The amount is small, but the research marks the figure as unverified.
- **Suggested fix:** state the billing assumption in §5.2. Prefer monthly billing until the domain proves stable.

---

## Category 4: Company proof points

### R-07 (High, Cat 4): no case-study or proof library
- **Source:**
  - CO L24–35: 9 case studies, including:
    - Legal AI: an AI legal assistant built for a legal-tech client;
    - Wish Wheels: CRM, orders, inventory, vendors, workflow automation and analytics for an automotive retail and services business;
    - BBG: order management;
    - MP Online Digital: workflow automation and dashboards.
  - CO L37–38: claimed differentiators, such as "built to last, not to demo" and "AI where it counts, never for hype".
  - CO L40–45: Legal AI is the angle for legal firms; Wish Wheels for businesses running on spreadsheets.
  - CO L10: 17+ engineers.
  - CO L49: the case studies have no numbers.
  - ICP L27: partners ask for references.
  - B L45: the "Nextron proof" row.
  - BADD §D L69: each follow-up adds "a relevant case study".
- **PRD location:**
  - FR-4 L114: each Step's "allowed content" isn't specified.
  - FR-20 L211–213: grounding uses Lead Evidence only.
  - FR-21 L216: made-up metrics are banned.
  - FR-22 L220: Segment template.
  - FR-43 L318: "approved company facts", which the PRD never defines.
- **Gap:**
  - No requirement names or stores Nextron's proof points, so follow-ups have no approved material, and claims about Nextron aren't grounded.
  - The grounding check covers only claims about the Lead.
  - Exact wording matters:
    - Legal AI's client is a legal-tech company, not a law firm.
    - Wish Wheels is in automotive retail and services, not distribution.
  - Loose wording from the AI, such as "we built this for a law firm like yours", would be a false claim. That is the brand risk the PRD is built to prevent.
- **Suggested fix:**
  - Add an FR (in §4.1 or §4.6) for a Proof Library. One entry per case study, holding:
    - approved one-line wording;
    - the Segments it may be used for;
    - any approved metric (none exist today);
    - whether the client can act as a reference.
  - Seed it with:
    - Legal AI for Law-PrivateAI;
    - Wish Wheels, plus BBG's order management and MP Online's workflow automation, for Ops-Automation;
    - team size and the San Jose HQ as company facts.
  - Set the Step 2–4 writing briefs in FR-4 to use one proof item each, as BADD §D says.
  - Extend FR-21 so any claim about Nextron must match a Proof Library entry, and define FR-43's "approved company facts" as this library.

### R-23 (Low, Cat 4): launch prerequisites from the company profile are missing
- **Source:**
  - CO L47–52: gaps that hurt conversion:
    - no booking link;
    - case studies without numbers;
    - no social links;
    - a site that search engines and link previews can't read;
    - no pricing or entry offer.
  - B L109–114: prerequisites include 1–2 case studies rewritten with numbers, and an updated LinkedIn profile and company page.
- **PRD location:**
  - §9 OQ-2 and OQ-3: postal address and booking link only.
  - §6.
  - FR-21 L216: bans made-up metrics.
- **Gap:** the PRD carries over only the booking link and the postal address. Missing:
  - **Case studies with numbers.** Without them, FR-21 correctly blocks metrics, so the proof is weak.
  - **LinkedIn profile and company page.** Prospects check these before replying, and the LinkedIn Tasks depend on them.
  - **A site crawlers can read.** Prospects who search for "Nextron AI" or see a link preview find nothing, and the lookalike domain redirects there.
- **Suggested fix:**
  - Add a short "Launch prerequisites" list to §9, with an owner for each item:
    - 1–2 case studies with numbers, which feed R-07's Proof Library;
    - Furqaan's LinkedIn profile and the Nextron company page;
    - text on nextronai.com rendered on the server or ahead of time.
  - Mark which items block the first send and which only lower conversion.

---

## Checked and consistent (no action needed)

| Research rule | Where the PRD reflects it |
|---|---|
| Never send cold email from nextronai.com (LAND L16) | FR-32, §5.5. R-02 adds subdomains |
| 25–50 sends per inbox per day (LAND L13) | FR-33 tops out at 30 |
| First email under 80 words, one call to action, 4–7 touches (LAND L50) | FR-4: under 80 words, one CTA, 4 Steps |
| No LinkedIn automation or scraping (LAND L35) | FR-49, §7 |
| CAN-SPAM opt-outs within 10 business days (LAND L40) | §5.1: "immediately" |
| UK: corporate subscribers only (LAND L41) | FR-2 and FR-12. Blocking Scottish partnerships, which PECR treats as corporate subscribers, is cautious and acceptable |
| Gulf, Germany, Canada, EU and India blocked in v1 (LAND L46) | FR-2, §6.2 |
| SMTP verification needs port 25 (LAND L54) | ADD §2 L25 |
| Companies House: 600 requests per 5 minutes (LAND L27) | ADD §2 L23 |
| Benchmarks (LAND L49–51) | SM-3's 1.5% positive reply rate sits at the top of the average band. SM-4's 4% reply rate sits between the average (3.43%) and the top quarter (5.5%): ambitious but consistent. The research finding that the first email produces 58% of replies supports OQ-7 (batch approval of follow-ups) |
| Ops revenue band (ICP L50: $10M–$500M) | FR-1: $10M–$200M, a deliberate narrowing taken from the brief |

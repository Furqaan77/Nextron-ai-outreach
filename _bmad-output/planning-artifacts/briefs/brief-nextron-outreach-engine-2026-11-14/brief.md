---
title: "Product Brief: Nextron Outreach Engine"
status: final
created: 2026-11-14
updated: 2026-11-14
inputs:
  - docs/research/nextron-company-profile.md
  - docs/research/outreach-landscape-2026.md
  - docs/research/icp-segments-2026.md
---

# Product Brief: Nextron Outreach Engine

## Executive Summary

Nextron AI is a 17-person custom software and AI agency in San Jose. It has nine delivered engagements, no live clients, and has never run outbound sales. Its pipeline is empty, and nothing in the business is designed to fill it.

The Nextron Outreach Engine is an in-house system that runs on one Windows PC for under $100 a month. It finds companies that show public signs of a problem Nextron solves, then writes short outreach that cites that evidence, in the name of CEO Furqaan Ahmed. The operator approves each batch. The engine then sends emails and follow-ups, detects replies, and hands interested prospects to Furqaan for a discovery call. It does not try to "scan every business." It goes after the few hundred companies a month most likely to buy a $10k–$30k first project, and says something specific to each one. In v1 it targets two segments in the US and UK: law firms with 5–50 lawyers, and mid-size distributors, logistics firms and light manufacturers.

**The engine succeeds only if it produces paying clients.** Every scope decision below is judged by whether it shortens the path to the first signed engagement.

## The Problem

- **Nextron has no pipeline.** New work has come from relationships, and those have dried up. There is no outbound process, no lead list and no sales tooling.
- **Generic cold outreach fails in 2026.** The average B2B cold-email reply rate is about 3.4%, and the median is 2.1%. The senders who do well use tight segments, emails under 80 words, one call to action and a specific reason for reaching out. A broad "we build any software for anyone" message ends up in the bottom half.
- **Off-the-shelf tools cost too much.** A typical outbound stack (Apollo + Clay + Instantly + LinkedIn automation) costs $300–$1,000+ a month.
- **The platforms limit what can be automated.**
  - LinkedIn prohibits automated messaging.
  - Instagram and WhatsApp let a business message only people who engaged first or opted in.
  - Cold email needs consent in the UAE, Saudi Arabia, Germany and largely Canada.

  Fully automated "message everyone everywhere" outreach is not available at any price without account bans or legal exposure.

## Who This Serves

**Operators:** Furqaan Ahmed (CEO) and Mac. Furqaan's name is on every message, and he takes discovery calls. Mac sets up and runs the engine. [ASSUMPTION: either of them approves batches; single shared user, no multi-user roles.]

**Target buyers (v1: US and UK):**

| | Segment 1: Private AI for law firms | Segment 2: Operations automation for mid-size companies |
|---|---|---|
| Who | Firms with 5–50 lawyers; managing partner | Distributors, logistics firms and light manufacturers with $10M–$200M revenue; COO or VP Operations |
| Why now | ABA Formal Opinion 512 requires informed consent before client data goes into AI tools. The Solicitors Regulation Authority (SRA) issued an AI notice in August 2026. These firms are too small for Harvey, a legal-AI platform with a ~20-seat minimum | 85% of inventory operators run on spreadsheets; Microsoft Dynamics GP support ends in 2029; 70% of mid-market firms say they need outside help |
| Observable signals | Hiring intake or legal-ops staff; website has only a contact form; reviews about unreturned calls | Job posts for data entry, AP clerks or dispatchers; Dynamics GP or another legacy system; news of a new facility or acquisition |
| Nextron proof | Legal AI case study | Wish Wheels business-management platform |
| Why chosen | Fastest to a first yes (one decision-maker) | Largest accounts: a $10k–$30k first build can grow into a $40k–$200k platform |

## The Solution

A local pipeline with one human checkpoint:

1. **Find:** pull businesses in the target segments from public, API-accessible sources: Google Places, company websites, UK Companies House and public job boards.
2. **Read the signals:** check each company's website, job posts and reviews for the segment signals above. Identify the decision-maker, then find and verify their email address.
3. **Score:** rank leads by fit (segment, size, market) and by signal strength. Only the top leads move forward.
4. **Draft:** Claude writes a four-email sequence that cites the observed signal, plus a LinkedIn connection note and a first message. Drafts may use only facts the engine actually collected.
5. **Approve:** a local web dashboard shows each batch with the evidence behind every message. The operator approves, edits or rejects each one.
6. **Send:** approved emails go out from warmed inboxes on a separate lookalike domain, within daily caps per inbox, during the recipient's business hours. Follow-ups stop when a prospect replies or unsubscribes. Opt-outs go on a permanent suppression list.
7. **Respond:** the engine detects and classifies replies as interested, not now, unsubscribe or out of office. Interested leads go to Furqaan with a booking link, a drafted reply and, optionally, a drafted WhatsApp, Instagram or LinkedIn follow-up.
8. **Learn:** a funnel view by segment and message variant shows sent, bounced, replied, positive, call booked and won.

**LinkedIn assist (runs alongside the pipeline):** a daily queue of about 20 decision-makers with drafted notes, which the operator sends by hand within safe weekly limits. The operator can also paste in any LinkedIn post that reveals a pain point; the engine turns it into an enriched lead and a draft.

## The Offer

[ASSUMPTION: chosen by Claude at Mac's request; validate on the first 10 calls]

- **Cold-email ask:** a low-effort reply ("worth a 20-minute look?") tied to the specific signal observed.
- **On the call:** a fixed-fee *Private AI & Workflow Audit* at $2,500–$3,500, credited in full toward a build.
- **Build:** $10k–$30k for a first project, such as a private document assistant, an intake or voice agent, or an operations-automation sprint.

## Success Criteria

| Signal | Target | Why it matters |
|---|---|---|
| **Paid engagement signed** | **First within 90 days of the first cold email; then at least 1 a month** | The only goal |
| Discovery calls booked | 4+ a month from month 2 | Leading indicator of revenue |
| Positive reply rate | 1.5%+ of contacted prospects | Shows targeting and offer fit |
| Total reply rate | 4%+ of contacted prospects | Shows message quality |
| Deliverability | Bounces under 2%; spam complaints under 0.1%; no domain blacklisting | Protects the sending channel |
| Operator effort | Approval takes under 30 minutes a day | Keeps the human checkpoint sustainable |
| Running cost | Under $100 a month (expected about $35–50) | Hard constraint |

**Rough funnel math** at about 90 emails a day once inboxes are warm:
- about 1,800 sends a month reach 500–600 prospects;
- that produces 20–30 replies, of which 8–10 are positive;
- which gives 4–5 calls and about 1 client a month at a 20–25% close rate.

[ASSUMPTION: industry-average rates; better targeting is the main way to beat them.]

## Scope

**In v1:**
- everything in *The Solution*, for the two segments, in the US and UK;
- cold email, fully automated after approval;
- LinkedIn, assisted (a human sends);
- WhatsApp and Instagram only for leads who have already replied;
- compliance basics: one-click unsubscribe, a postal address, a suppression list, a record of where each lead came from, and UK email only to companies and LLPs (details in the addendum, section B).

**Out of v1:**
- Bulk scraping of LinkedIn or Instagram, or automated LinkedIn actions.
- Cold Instagram DMs and cold WhatsApp messages.
- Cold email to the UAE, Saudi Arabia, Germany and Canada.
- Other segments (CPA firms, investment advisers, clinics, real estate) and other markets (Gulf, India, EU); see *Vision*.
- Cloud hosting, 24/7 operation and multi-user access.
- Automated calling.
- Paid lead databases beyond free tiers.
- A full CRM; a simple pipeline view is enough.

**Prerequisites outside the engine.** The engine can't convert prospects without these:
- a sending domain and 2–3 inboxes, bought and warming for 2–4 weeks;
- a free booking link (e.g. Cal.com) to replace the website's current mailto link;
- a business postal address for the email footer;
- 1–2 case studies rewritten with numbers;
- an updated LinkedIn profile for Furqaan and company page for Nextron.

## Risks and Open Questions

- **Warm-up timing.** The domain hasn't been bought yet. Every week of delay pushes the first send back a week.
- **Credibility.** Law partners ask for references, and the current case studies have no metrics. To offset this, lead with the regulatory angle and offer the audit as a low-risk first step.
- **The PC must be on during US and UK business hours.** Mac has confirmed it will be. If the PC is off or asleep, no emails go out, so the engine must catch up safely: no burst sends and no duplicates.
- **Email verification.** Home internet providers usually block port 25, so the engine can't verify addresses over SMTP. It must rely on free API tiers (Hunter: 50 credits a month), MX checks and bounce monitoring. That limits how many addresses can be verified and puts the under-2% bounce target at risk.
- **AI accuracy.** A personalized message that cites a wrong "fact" damages trust. Every message uses only stored evidence, and the approval step exists to catch errors.

## Vision

If v1 wins clients, the same engine expands to more segments and markets:
- **Segments:** CPA firms, registered investment advisers (RIAs) and clinics, using the private-AI pitch, plus real estate.
- **Markets:** the Gulf and India through LinkedIn and referral-led sequences, where Nextron's real estate, infrastructure and government case studies already are; the EU once consent routes exist.

Nextron could then use the engine as its own case study, or sell it as a service: "an AI outbound engine for your firm, built like the one that found you."

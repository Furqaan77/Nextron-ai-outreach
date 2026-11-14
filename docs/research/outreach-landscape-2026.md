# Outreach Landscape: Constraints & Benchmarks (researched 2026-11-14)

Web-research digest gathered for the Nextron Outreach Engine brief and PRD. "⚠" marks a claim that could not be verified or where sources conflict. Unless marked official, a source is a vendor blog.

## 1. Email deliverability
- **Gmail** ([official](https://support.google.com/a/answer/81126))
  - Every sender: SPF or DKIM, valid PTR, TLS, spam rate under 0.3% (target under 0.1%).
  - 5,000+ emails/day: SPF + DKIM + DMARC (p=none is enough) with the From domain aligned, plus one-click unsubscribe.
  - Enforcement tightened in Nov 2025 with temporary and permanent rejections.
- **Yahoo** ([source](https://senders.yahooinc.com/best-practices)): SPF + DKIM, DMARC aligned, RFC 8058 one-click unsubscribe, complaint rate under 0.3%.
- **Outlook.com** ([official, Apr 2025](https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730)): above 5,000/day, SPF + DKIM + DMARC are required. Non-compliant mail has gone to Junk since May 2025, and rejection with error 550 5.7.515 is planned.
- **Cold-email practice** (vendor consensus, [source](https://leadhaste.com/blog/cold-email-sending-limits-2026))
  - 25–50 sends per inbox per day.
  - 2–4 week warm-up, starting at 5–10/day.
  - About 3 inboxes per domain.
  - **Use separate lookalike domains; never send cold email from nextronai.com.**
- **Inbox cost:** Google Workspace Starter about $7/user/mo on annual billing ($8.40 monthly) ⚠ USD figure. Microsoft 365 Business Basic $7. Exchange Online Plan 1 $4.

## 2. Lead and contact data (free tiers)
| Source | Free tier | API on free tier? |
|---|---|---|
| Google Places API (New) | Text Search Pro (name/address): 5,000/mo free. Enterprise fields (website/phone/rating): 1,000/mo free, then $35 per 1,000 | Yes |
| Hunter.io | 50 credits/mo (find = 1, verify = 0.5); Domain Search returns at most 10 results. Starter is $49/mo, or $34/mo annual, for 2,000 credits | Yes |
| Prospeo | 100 credits/mo | Limited |
| Apollo.io | About 75 credits/mo ⚠ | Unclear ⚠ (docs list Free-plan rate limits; pricing page says API is on Custom plans) |
| Snov.io | 50 credits trial | No |
| UK Companies House | Free key, 600 requests per 5 min | Yes |
| SEC EDGAR | Free, 10 requests/s, User-Agent header required | Yes |
| OpenStreetMap Overpass | Light use only; commercial users should self-host | Yes (fair use) |
| Greenhouse / Lever / Ashby job boards | Public endpoints, no key (hiring signals) | Yes |
| OpenCorporates | From £2,250/yr | No |
| India MCA | No official public API ⚠ | — |

## 3. Social and messaging channels (these limits decide the channel design)
- **LinkedIn:** User Agreement §8.2 bans bots, scraping and automation tools, and accounts get restricted. The Messages API is for approved partners only, and each message must come from a member's own action. Vendors report about 100 connection invites/week as a safe level ⚠. **Implication: the engine drafts LinkedIn messages and a human sends them.**
- **Instagram:** a business can message only after the user acts first (24-hour window; one private reply to a comment within 7 days). **Implication: no cold Instagram DMs through the API. Instagram is a warm-engagement channel only.**
- **WhatsApp Business:** you may message only people who gave you their number and opted in; templates are required outside the 24-hour window (policy updated Sep 2026). **Implication: WhatsApp is only for leads who have replied or shared their number.**

## 4. Legal basics for B2B cold email
- **US (CAN-SPAM):** no consent needed. Requires truthful headers and subject, a postal address, and opt-outs honoured within 10 business days. Fines up to $53,088 per email.
- **UK (PECR):** corporate subscribers can be emailed without consent if you identify yourself and give an opt-out. Sole traders and some partnerships need consent. UK GDPR covers named contacts.
- **EU:** GDPR legitimate interest needs a balancing test; each country's ePrivacy law decides whether you can email at all. **Germany requires prior consent even for B2B.**
- **Canada (CASL):** consent is required. The conspicuous-publication exception applies only if the address was published by the recipient, carries no "no solicitation" notice, and the message is relevant to their role. Address-harvesting software is banned.
- **UAE (PDPL + TDRA) and KSA (PDPL):** consent is required for direct marketing; no B2B exception found.
- **India:** no email-specific anti-spam law; DPDP Rules consent duties apply from about May 2027.
- **Implication:** cold email works for US, UK (companies), most of the EU (with a legitimate-interest assessment) and India. Canada needs conspicuously published addresses only. Gulf and Germany need LinkedIn, warm or referral routes rather than cold email.

## 5. Benchmarks
- **Instantly 2026 report:** average reply rate 3.43%, top quarter 5.5%+, top 10% 10.7%+. The first email produces 58% of replies.
- **What top senders do:** tight segments, first email under 80 words, one call to action, 4–7 touches, and signals such as hiring, funding or launches.
- **Positive replies:** about 0.5–1.5% on average, 3%+ for the best senders. Agencies see about 3–5% reply rate; the 2026 B2B median is 2.1%.

## 6. Reusable open-source components
- **Reacher check-if-email-exists** (AGPL-3.0): SMTP-level verification, **needs outbound port 25, which most home ISPs block.**
- **gosom/google-maps-scraper** (MIT): scrapes the Maps website rather than using the API; Terms-of-Service risk is unverified ⚠.
- **Mautic** (marketing automation) and **listmonk** (newsletters): neither is built for cold sequences. Smaller cold-outreach projects are unverified ⚠.

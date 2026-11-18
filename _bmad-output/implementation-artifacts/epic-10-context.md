# Epic 10 Context: LinkedIn assist and warm channels

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

LinkedIn stays human-operated: the engine only prepares the work and keeps volume safe. Each weekday the LinkedIn account owner (default Furqaan) gets a short, prioritised list of LinkedIn Tasks on Telegram, one message per Task, and sends them by hand within per-account daily and rolling-week caps. The Operator (Mac) marks outcomes on the dashboard. For Hot Leads, Mac can request drafted LinkedIn, WhatsApp or Instagram messages and call sheets, then log each manual touch. WhatsApp and Instagram need a recorded Channel Permission. Every path passes the single contactability gate, and contracts guarantee the engine never automates or fetches LinkedIn or Instagram.

## Stories

- Story 10.1: Prove by contract that LinkedIn and Instagram are never automated
- Story 10.2: Configure the LinkedIn account, its owner's Telegram chat and its caps
- Story 10.3: Draft LinkedIn connection notes and first messages
- Story 10.4: Prepare each weekday's LinkedIn Tasks in priority order
- Story 10.5: Deliver each LinkedIn Task to the owner's Telegram chat
- Story 10.6: Mark Tasks sent, skipped or accepted and log found profile URLs
- Story 10.7: Record Channel Permissions for WhatsApp and Instagram
- Story 10.8: Draft warm-channel messages for Hot Leads and log each touch
- Story 10.9: Prepare a call sheet for a Hot Lead

## Requirements & Constraints

- **No automation, ever.** The engine never signs in to LinkedIn, never automates a browser on LinkedIn or Instagram, and never fetches LinkedIn pages. LinkedIn URLs come only from the Signal Inbox, the Lead's website, a CSV import, a referral or an Operator entry, each recorded in Provenance. A stored URL is never fetched.
- **Human actions only.** Marking a Task `sent` or `accepted`, or a channel Draft `sent`, is done only by web handlers acting for the Operator. No job, adapter or scheduled trigger may do it. Telegram is send-only: no `getUpdates` and no webhook.
- **Volume.** By default 10 Tasks per weekday, a daily invitation cap of 10, and at most 100 invitations per account in a rolling 7×24-hour window. Scheduled invitations are Tasks in `pending`, `sent` or `accepted`. Caps are stored per account. No Tasks are prepared on a Saturday or Sunday in `sender_timezone`.
- **One account in v1.** Only one LinkedIn account may be active, and its Tasks go only to the `linkedin_owner_chat` setting, never to `alert_chat_id`. Drafts use the owner's voice (`owner_name`, `owner_role`).
- **Priority order.** Signal Inbox Leads (Evidence from the last 7 days) come first, then Hot Leads (latest Reply hot, stage open), then Tier A Leads whose Step 1 was sent 1–2 business days ago. Within a group, newest first. A Contact is never duplicated. A `skipped` Task blocks a new one for 30 days. A `pending` Task expires after 5 business days.
- **Channel Draft limits.**
  - Connection note: optional, at most 200 characters, with no URL, price, Offer fact or meeting request.
  - First message: at most 300 characters, with no URL or price. It must cite something the sent emails didn't.
  - Warm Drafts: at most 300 characters. The only URL allowed is `booking_link`, plus the two proposed slots.
  - Banned patterns and the grounding check apply exactly as for email Drafts. Regenerating or editing re-runs grounding.
- **Channel Permission.** A WhatsApp or Instagram Draft needs an active permission recording what the prospect did, when (not in the future), and Evidence text of 300 characters or fewer. An email reply, or a number in a signature or on a website, doesn't qualify. Instagram has a 24-hour window from `last_inbound_at`: once it closes, the Draft can't be logged as sent.
- **Telegram content minimisation.** A Task message holds only the Task reference, "n of N today", the profile URL or a search hint, the Contact's name and role, the Lead name, the note and the first message. It never holds an email address, phone number, Evidence, Provenance or another Contact. It is plain text with no `parse_mode`.
- **Cancellation and erasure.** Suppression cancels pending Tasks and their Drafts in the same transaction. For a delivered Task, `deleteMessage` is tried if the message is under 48 h old; otherwise a "don't send" follow-up with no personal data goes to the same chat. On erasure, Task alerts older than 48 h are listed as residuals on the receipt.
- UK Leads that are ineligible for cold email can still get LinkedIn Tasks.

## Technical Decisions

- **Contactability gate.** `compliance.assert_contactable(subject, channel, purpose)` is the only suppression oracle.
  - Channels: `linkedin`, `whatsapp`, `instagram`, `phone`.
  - Purposes: `task`, `follow_up`, `call`.
  - It runs before creating, listing or delivering a Task, before drafting any channel Draft or call sheet, before recording a permission, and before logging a touch.
  - A `Blocked` result creates nothing. Each new path adds a scenario to the AD-16 callers contract.
  - A LinkedIn profile is a suppression key: `profile` = `normalize_linkedin_url`.
- **Ownership.**
  - `pipeline` owns `linkedin_accounts`, `linkedin_tasks`, `channel_permissions` and `touches`, plus the commands `cancel_tasks` (the `SubjectSuppressed` subscriber) and `record_call_outcome`.
  - `drafting` owns channel Drafts (`request_channel_draft`, `request_call_prep`, `call_preps`).
  - `research` stores the LinkedIn URL and Evidence.
- **State machines.**
  - LinkedInTask (`pipeline`): `pending`, `sent`, `skipped`, `accepted`, `cancelled`. An illegal transition is refused with "This Task can't move from X to Y".
  - Channel Drafts use the Draft machine: `ready → sent` only when the Operator logs the touch. Suppression or revocation cancels them (`ready → cancelled`).
- **Channel Drafts never become email.** `sending.enqueue` and `approval.approve` refuse kind `channel`.
- **AI routes.** `draft_channel` and `call_prep` (Opus, `essential`). Lead Evidence goes in `<untrusted>` blocks. Outputs cite Evidence ids and fact versions. Grounding regenerations run at `interactive` urgency. In a call sheet, the phone number is inserted by code, never by the model: a phone number or URL in model output fails `grounding_rules`.
- **Jobs.**
  - `prepare_linkedin_tasks:global:<local date>` (`essential`) is chained after `daily_selection` and also enqueued when the Signal Inbox gives a Lead a Primary Contact. It is idempotent.
  - `deliver_linkedin_task:<task ulid>` (realtime lane) has `run_after` set to the start of working hours (default 09:00 `sender_timezone`). While grounding is `pending`, it re-runs after 5 min without using a retry attempt.
- **Alerts.** Delivery goes through `notify.alert(kind=linkedin_task, subject=<task ulid>, channel=telegram)`. `AlertPort` is the only channel to Nextron people. Outside `prod`, the sink delivers only to allowlisted chats.
- **Pure domain helpers.** `domain.linkedin_caps.remaining(account, tasks, now, tz)` returns the minimum of the three cap headrooms, never negative, and is unit-tested with a frozen `now`. `domain.identity.normalize_linkedin_url` is the only normaliser; a logged profile URL must yield `https://www.linkedin.com/in/...`.
- **FR-49 enforcement.**
  - `test_no_linkedin_adapter`: no module named `linkedin` or `instagram` outside an allowlist of domain modules, and no port outside the AD-9 list.
  - The import-linter forbids `linkedin_api`, `instaloader`, `instagrapi`, `selenium`, `pyautogui`, `undetected_chromedriver` and `linkedin`, and a test asserts none of them is in `uv.lock`.
  - The crawler and the Playwright renderer abort requests to the hard-coded denylist: `linkedin.com` and its subdomains, `lnkd.in`, `instagram.com`.
- **Settings.** Add `linkedin_owner_chat` to `settings_schema`. A missing active account or a blank chat raises the health issue "LinkedIn Tasks off: …" and no Task is prepared.

## UX & Interaction Patterns

- **Settings → LinkedIn section.** Account fields with server-side validation. Refusal messages include "At most 100 invitations per account in a rolling week" and "v1 supports one LinkedIn account; …".
- **LinkedIn Tasks page.**
  - Lists today's Tasks and older `pending` ones, each with its group, profile link (`http`/`https` only, `rel="noopener noreferrer"`) or search hint, note, first message, status and delivery time.
  - Shows the invitations left today and in the rolling week.
  - Actions: sent, skipped (optional reason), accepted, and log a found URL. Every action is a POST with CSRF.
  - A Task whose Draft failed grounding shows "Draft failed the check: edit or regenerate, then deliver" with a "Deliver now" button.
- **Warm follow-up panel on a Hot Lead.**
  - Actions: LinkedIn, WhatsApp, Instagram, plus a call sheet. A disabled action states why.
  - Instagram cards show "Window closes at <time>", then "Window closed: don't send".
  - "Logged as sent" is available only for passing Drafts.
  - "Log a touch" takes a channel, a kind and a note of up to 1,000 characters.
  - Touches appear in the Lead's history timeline.
- **Channel Permission form on a Contact.**
  - Fields: channel, a prospect action valid for that channel, date and time, and Evidence text.
  - States "An email reply, or a number in a signature or on a website, doesn't qualify".
  - Supports revoke, updating `last_inbound_at`, and "Opted out on this channel". That last action calls `compliance.suppress` on every key for the person.
- **Call log.** Outcomes are `connected`, `voicemail` or `no_answer`, with an optional note. `connected` offers "Book a call", which links to the `call_booked` move.

## Cross-Story Dependencies

- 10.2 (accounts, caps, setting) and 10.3 (channel Draft columns and drafting) come before 10.4. 10.4 (`linkedin_tasks`) comes before 10.5 and 10.6. 10.6 creates `touches`, which 10.8 and 10.9 reuse. 10.7 (permissions) comes before 10.8's WhatsApp and Instagram actions and 10.9's phone-number source.
- **From earlier epics:**
  - Epic 3: the suppression gate, `SubjectSuppressed`, and erasure with external receipts.
  - Epic 4: the grounding rules, the grounding check and the Draft machine.
  - Epic 5: `approval`.
  - Epic 7: Hot Reply classification and `notify`/`AlertPort` Telegram.
  - Epic 8: `daily_selection` and the Signal Inbox chain.
  - Epic 9: `call_preps`, `domain.slots.propose` and the pipeline stage moves.
  - Epic 1: the import-linter seed, the job runner and the health registry.
- **Open question (PRD OQ-5):** whose LinkedIn account sends the Tasks. v1 assumes one active account.

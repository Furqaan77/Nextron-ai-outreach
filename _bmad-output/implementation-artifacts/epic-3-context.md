# Epic 3 Context: Legal to contact: suppression, eligibility and opt-outs everywhere

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Prove, before anything is drafted or sent, that each Contact may lawfully be reached on each channel under CAN-SPAM, PECR and UK GDPR. The epic delivers:
- US and UK Market rules, and each Contact's Market and timezone;
- a permanent, hashed Suppression List with one writer, and one contactability gate on every outreach path;
- live Eligibility with every reason shown;
- one-click unsubscribe through a polled Cloudflare Worker;
- Provenance with the legitimate-interest basis;
- erasure and export, with a ledger that survives restores.

It is Slice 1 work, and later outreach epics depend on these gates.

## Stories

- Story 3.1: Seed the US and UK Market rules
- Story 3.2: Resolve each Contact's Market and timezone
- Story 3.3: Record suppression entries through one writer
- Story 3.4: Gate every outreach path with `assert_contactable`
- Story 3.5: Manage the Suppression List from the dashboard
- Story 3.6: Evaluate Eligibility live and show the reasons
- Story 3.7: Show Provenance and the Eligibility basis on the Lead page
- Story 3.8: Issue and decode unsubscribe tokens
- Story 3.9: Spike: confirm the Sending Domains' DNS zones can host the Worker (OQ-A7)
- Story 3.10: Deploy the unsubscribe Worker
- Story 3.11: Poll unsubscribe requests into the Suppression List
- Story 3.12: Monitor unsubscribe polling and re-poll the Worker after a restore
- Story 3.13: Erase a Contact on request
- Story 3.14: Erase personal data outside the database and issue a receipt
- Story 3.15: Export everything held on a Contact
- Story 3.16: Keep an erasure ledger and reapply it after a restore

## Requirements & Constraints

- **Markets:** only the US and UK are enabled. Markets load from seed YAML, so a simple new Market needs no code. A Lead's Market is derived from `country_code`, never stored.
  - **Per Contact:** both the office location and any country-code email domain must be in an enabled Market (`.de`, `.ca` and `.io` block). An unknown location falls back to the Lead's country. A clash between two enabled Markets takes the UK. Provenance notes each fallback.
  - **UK:** corporate subscribers with a confirmed legal form, never free webmail.
  - **US:** a Contact with no known state uses `America/New_York`, with the Send Window narrowed to 11:00–17:00 Eastern.
- **Eligibility:** computed live and never stored. Every failing check adds a reason from a closed enum. A re-contact needs 90 days or more since the last Step plus a newer Event.
- **Suppression:** entries never expire and are never deleted. Only a `reply_rule` entry can be voided, by the Operator, with a reason. A whole free-webmail domain is never suppressed. An opt-out takes effect within one polling cycle.
- **Can't be turned off:** no setting or route disables Market rules, Eligibility or suppression. Tests check this.
- **Unsubscribe Endpoint:** the only off-PC store, holding tokens and times only. It never deletes; entries expire after 30 days in KV. It is hosted on `u.<Sending Domain>`, never `*.workers.dev`.
  - The engine polls it every 5 min in `prod` and every 30 min elsewhere. Budget and quotas never block the poll, and 24 h of failures raises an alert.
  - KV list calls are limited to 1,000 a day: alert at 80%, never stop.
- **Erasure:** tombstones, never deletes. A DSR keeps hashed keys suppressed, including person and profile keys while OQ-A10 is open. An export is a single JSON download, never written to disk.
- **No personal data** goes into `events`, job payloads (ULIDs only), logs, unsubscribe tables or the ledger.

## Technical Decisions

- **Owners:**
  - `compliance`: suppression, unsubscribe tokens and requests, DSRs, receipts and the ledger.
  - `setup`: `markets`.
  - `research`: the Contact Market and `tz` columns and Provenance, including the eligibility basis.
  - Every column is mapped in `owners.py` and `pii_registry.py`.
- **Pure domain code (stdlib only, tested with a fixed `now`):** `domain.markets`, `domain.contactability`, `domain.eligibility.evaluate` and the SuppressionEntry machine (`active → voided`).
- **Keys:** `value_hash` = SHA-256 of `kind:` plus a value normalised only by `domain.identity`:
  - an address by `normalize_email`, with no plus or dot stripping;
  - a domain by `registrable_domain`;
  - a person by normalised name `|` web domain;
  - a profile by `normalize_linkedin_url`.

  There is one `active` row per key.
- **One writer, one oracle:** `compliance.suppress` is the only writer, covering every known key for the person and the domain keys for "remove us".
  - It publishes `SubjectSuppressed`. Its subscribers run before commit; `pipeline.close(lost)` runs only for opt-out sources.
  - `compliance.assert_contactable(subject, channel, purpose)` is the only check.
  - A recording-fake contract test covers its callers, skipping by name any not yet built.
- **Token:** `TokenCodecPort`, with a `cryptography` adapter, encodes base64url(`kid` ‖ nonce ‖ AES-GCM-SIV(`addr_hash` ‖ `issued_at`) ‖ 12-byte HMAC tag).
  - Decoding needs no DB.
  - Keys are kept in keyring per `kid`, and each `kid` stays valid for at least 2 years.
  - The shared vector fixture also binds the Worker.
- **Worker** (`workers/unsubscribe/`, wrangler 4.162.0):
  - POST records a token once; GET never writes.
  - `/pending` needs a bearer token.
  - Each environment has its own KV namespace; logs are off.
- **Polling:** the httpx2 `UnsubscribePort` is metered as `kv_list` and runs on the realtime lane.
  - Each fetch is applied in one transaction, deduplicated by `token_hash`. It counts as processed only after the commit.
  - Mailto opt-outs use the same path.
- **Erasure:** `compliance.erase_contact` runs in one unit of work.
  - A DSR suppresses first; retention doesn't.
  - Each owner's `erase_pii`, on `ContactErased`, tombstones by registry class.
  - A receipt is written, and one `erase_external` job is queued per store.
  - Contract tests require a hook and an exporter for every personal-data table and a handler for every store, and forbid `ON DELETE CASCADE`.
- **Ledger:** an append-only JSONL file in the backup dir, fsynced after each commit.
  - A failed append alerts and doesn't roll back.
  - It is re-synced at startup and copied with backups.
  - It is reapplied as a recovery step before the runner starts.

## UX & Interaction Patterns

- **Suppression List panel:**
  - add, import, exact-value search and filters;
  - rows show a hash prefix, never the address;
  - Reverse appears only on `reply_rule` entries, and there's no delete.
- **Lead page:** live Eligibility and its reasons, Provenance per Contact and address, the UK LIA basis, Erase and Export, tombstones and receipts.
- **All actions** are CSRF-checked POSTs. Untrusted text sits in `hx-disable` containers, with `Cache-Control: no-store`.
- **Health panel:** poll age, failed erasure handlers and a missing ledger.

## Cross-Story Dependencies

- **Epic 1:** units of work, settings, credentials, jobs, metering, alerts, and the doctor, health and recovery registries. Stories 3.12 and 3.16 need 1.17–1.19.
- **Epic 2:**
  - the Contact `tz` and Market columns and the Provenance basis fields;
  - legal-form Evidence and the Address Class;
  - the creation paths that 3.4 retrofits.
- **Within Epic 3:**
  - 3.1 comes before 3.2 and 3.6.
  - 3.3 comes before 3.4, 3.5, 3.11 and 3.13.
  - 3.8 comes before 3.10 and 3.11; 3.9 sets the Worker host.
  - 3.13 comes before 3.14, 3.15 and 3.16.
- **Later epics:**
  - Epic 4's `enroll` and drafting call the gate, and `enroll` records the eligibility basis.
  - Epic 5 calls the gate on approval and issues a token per OutboundMessage at enqueue (5.2).
  - Epic 6 adds the mailbox erasure handler (6.7) and the send gate (6.9).
  - Epic 7 routes reply and mailto opt-outs.
  - Epic 8 retention calls `erase_contact(cause=retention)`.

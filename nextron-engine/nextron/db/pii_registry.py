"""The personal-data registry (AD-17): every column of every model, classified.

``PII`` maps ``table -> {column: PiiClass}``. The class says what erasure does to the column:

- ``none``: holds no personal data (IDs, statuses, codes, times, counts); left as it is.
- ``redact``: free text that may hold personal data; becomes ``"[erased]"``.
- ``null``: personal data with no text to keep; set to ``NULL``.
- ``keep_hash``: personal data replaced by a hash, so suppression and dedupe still match.

Every column of ``nextron.db.models.Base.metadata`` is classified exactly once and nothing else
is listed; ``tests/contracts/test_persistence_contracts.py`` fails otherwise. ``events`` never
holds personal data, so every one of its columns is ``none``.
"""

from enum import StrEnum
from typing import Final


class PiiClass(StrEnum):
    NONE = "none"
    REDACT = "redact"
    NULL = "null"
    KEEP_HASH = "keep_hash"


PII: Final[dict[str, dict[str, PiiClass]]] = {
    "events": {
        "id": PiiClass.NONE,
        "entity": PiiClass.NONE,
        "entity_id": PiiClass.NONE,
        "from_status": PiiClass.NONE,
        "to_status": PiiClass.NONE,
        "cause": PiiClass.NONE,
        "cause_ref": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
    # Later keys hold Nextron staff addresses and the postal address (Story 1.6).
    "settings": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "value": PiiClass.REDACT,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.1. A Segment's definition holds role titles, keywords and rules, never a person.
    "segments": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "definition": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 3.1. A Market's rules and footer template hold codes, flags and placeholders, never
    # a person.
    "markets": {
        "id": PiiClass.NONE,
        "code": PiiClass.NONE,
        "name": PiiClass.NONE,
        "enabled": PiiClass.NONE,
        "country_code": PiiClass.NONE,
        "cctlds": PiiClass.NONE,
        "cold_email_allowed": PiiClass.NONE,
        "eligible_legal_forms": PiiClass.NONE,
        "free_webmail_blocked": PiiClass.NONE,
        "default_timezone": PiiClass.NONE,
        "unknown_timezone": PiiClass.NONE,
        "unknown_send_window": PiiClass.NONE,
        "english_variant": PiiClass.NONE,
        "footer_template": PiiClass.NONE,
        "privacy_line_required": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.1. A Sequence and its Step briefs hold rules, limits and angle texts, never a
    # person.
    "sequences": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "step_briefs": {
        "id": PiiClass.NONE,
        "sequence_id": PiiClass.NONE,
        "step": PiiClass.NONE,
        "delay_days": PiiClass.NONE,
        "purpose": PiiClass.NONE,
        "word_limit": PiiClass.NONE,
        "word_comparison": PiiClass.NONE,
        "allowed_content": PiiClass.NONE,
        "ask": PiiClass.NONE,
        "link_rule": PiiClass.NONE,
        "no_price": PiiClass.NONE,
        "no_audit": PiiClass.NONE,
        "subject_rule": PiiClass.NONE,
        "segment_angles": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.2. A Lead is an organization: its name, keys and country are firm data, so erasing a
    # Contact never tombstones the firm's Lead.
    "leads": {
        "id": PiiClass.NONE,
        "name": PiiClass.NONE,
        "web_domain": PiiClass.NONE,
        "ch_number": PiiClass.NONE,
        "place_id": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "country_code": PiiClass.NONE,
        "merged_into": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "pipeline_stage": PiiClass.NONE,
        "hq_tz": PiiClass.NONE,  # Story 2.9: a zone and an Evidence id, firm data
        "hq_evidence_id": PiiClass.NONE,
    },
    # Story 2.8. A prompt is the engine's own text, never a person's; an activation holds codes,
    # a hash and times.
    "prompt_snapshots": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "text": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "ai_route_activations": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "model": PiiClass.NONE,
        "effort": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "prompt_sha256": PiiClass.NONE,
        "schema_version": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "bootstrap": PiiClass.NONE,
        "test_run_id": PiiClass.NONE,
        "activated_at": PiiClass.NONE,
        "deactivated_at": PiiClass.NONE,
    },
    # Story 2.3. A Contact is a person: free text is redacted, the address is kept as a hash so
    # suppression and dedupe still match after an erasure, and the LinkedIn URL is nulled.
    "contacts": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "name": PiiClass.REDACT,
        "role": PiiClass.REDACT,
        "email": PiiClass.KEEP_HASH,
        "address_class": PiiClass.NONE,
        "linkedin_url": PiiClass.NULL,
        "is_primary": PiiClass.NONE,
        "tz": PiiClass.NONE,
        "market_id": PiiClass.NONE,
        # Story 3.2: why the Market and timezone apply (codes only), and the office's place
        "market_basis": PiiClass.NONE,
        "tz_basis": PiiClass.NONE,
        "office_country": PiiClass.NULL,
        "office_region": PiiClass.NULL,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.3. A record's source detail (a CSV's origin, a URL) and notes are free text; its
    # address is the Contact's address at the time, kept as a hash like contacts.email.
    "provenance_records": {
        "id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "source_kind": PiiClass.NONE,
        "source_detail": PiiClass.REDACT,
        "address": PiiClass.KEEP_HASH,
        "retrieved_at": PiiClass.NONE,
        "address_class": PiiClass.NONE,
        "notes": PiiClass.REDACT,
        "basis_rule": PiiClass.NONE,
        "basis_campaign_id": PiiClass.NONE,
        "lia_ref": PiiClass.NONE,
        "lia_date": PiiClass.NONE,
        "basis_recorded_at": PiiClass.NONE,
    },
    # Story 4.2. Offers, Campaigns and Variants are configuration; the LIA reference names the
    # Operator's own assessment record, never a person.
    "offers": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "ladder": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "campaigns": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "market_id": PiiClass.NONE,
        "offer_id": PiiClass.NONE,
        "sequence_id": PiiClass.NONE,
        "status": PiiClass.NONE,
        "lia_ref": PiiClass.NONE,
        "lia_date": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "variants": {
        "id": PiiClass.NONE,
        "campaign_id": PiiClass.NONE,
        "name": PiiClass.NONE,
        "status": PiiClass.NONE,
        "step_angles": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.3. Approved Company Facts are claims about Nextron, its work, its Offer and public
    # regulation, never about a person.
    "company_facts": {
        "id": PiiClass.NONE,
        "fact_id": PiiClass.NONE,
        "version": PiiClass.NONE,
        "type": PiiClass.NONE,
        "wording": PiiClass.NONE,
        "source_url": PiiClass.NONE,
        "allowed_segment_ids": PiiClass.NONE,
        "allowed_market_ids": PiiClass.NONE,
        "metric": PiiClass.NONE,
        "metric_confirmed_at": PiiClass.NONE,
        "scope_note": PiiClass.NONE,
        "last_checked_on": PiiClass.NONE,
        "created_by": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
    },
    # Story 4.7. Nextron's own sender identity (its Sending Domains, its inboxes' addresses,
    # labels and From name), never a data subject's: erasing a Contact never rewrites it.
    "sending_domains": {
        "id": PiiClass.NONE,
        "domain": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "sending_inboxes": {
        "id": PiiClass.NONE,
        "address": PiiClass.NONE,
        "label": PiiClass.NONE,
        "sending_domain_id": PiiClass.NONE,
        "from_name": PiiClass.NONE,
        "status": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    # Story 2.5. A page's visible text may quote a person, so it is redacted; the URLs are of an
    # organization's public pages, and the rest are IDs, codes, counts, hashes and times.
    "crawl_runs": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "site_domain": PiiClass.NONE,
        "final_domain": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "pages_fetched": PiiClass.NONE,
        "pages_skipped_robots": PiiClass.NONE,
        "renders": PiiClass.NONE,
        "errors": PiiClass.NONE,
    },
    "crawled_pages": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "run_id": PiiClass.NONE,
        "url": PiiClass.NONE,
        "final_url": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "fetched_at": PiiClass.NONE,
        "http_status": PiiClass.NONE,
        "content_hash": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "text_purge_after": PiiClass.NONE,
        "changed_at": PiiClass.NONE,
    },
    "ats_links": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "run_id": PiiClass.NONE,
        "page_id": PiiClass.NONE,
        "url": PiiClass.NONE,
        "host": PiiClass.NONE,
        "tag": PiiClass.NONE,
        "first_seen_at": PiiClass.NONE,
        "last_seen_at": PiiClass.NONE,
    },
    # Story 2.9. A quote or a fact's value is a page's words and may quote a person, so both are
    # redacted; the rest are IDs, codes, numbers, public page URLs and times.
    "evidence": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "type": PiiClass.NONE,
        "quote": PiiClass.REDACT,
        "sought": PiiClass.NONE,
        "number": PiiClass.NONE,
        "fact": PiiClass.NONE,
        "value": PiiClass.REDACT,
        "pages_checked": PiiClass.NONE,
        "source": PiiClass.NONE,
        "source_url": PiiClass.NONE,
        "retrieved_at": PiiClass.NONE,
        "extracted_by": PiiClass.NONE,
        "person_contact_id": PiiClass.NONE,
        "superseded_by": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
        "last_confirmed_at": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "signals": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "signal_type": PiiClass.NONE,
        "signal_class": PiiClass.NONE,
        "tier": PiiClass.NONE,
        "score_only": PiiClass.NONE,
        "event_date": PiiClass.NONE,
        "first_seen_at": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
    },
    "signal_evidence": {
        "id": PiiClass.NONE,
        "signal_id": PiiClass.NONE,
        "evidence_id": PiiClass.NONE,
    },
    "extraction_runs": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "crawl_run_id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "pages_sent": PiiClass.NONE,
        "pages_truncated": PiiClass.NONE,
        "stored": PiiClass.NONE,
        "discarded": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
    },
    # Payloads and keys hold IDs and scalars only (AD-10, AD-17). last_error is free text: an
    # exception's message is redacted and cut to 500 characters, but could still name a person.
    "jobs": {
        "id": PiiClass.NONE,
        "type": PiiClass.NONE,
        "lane": PiiClass.NONE,
        "status": PiiClass.NONE,
        "priority": PiiClass.NONE,
        "payload": PiiClass.NONE,
        "idempotency_key": PiiClass.NONE,
        "concurrency_key": PiiClass.NONE,
        "run_after": PiiClass.NONE,
        "attempts": PiiClass.NONE,
        "lease_until": PiiClass.NONE,
        "last_error": PiiClass.REDACT,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # The engine's process id and two times (Story 1.11).
    "runner_heartbeat": {
        "id": PiiClass.NONE,
        "pid": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "beat_at": PiiClass.NONE,
    },
    # Story 1.16. The copy's path, size, hash, revision, counts and times. error is free text:
    # an exception's message, redacted and cut to 500 characters, could still name a person.
    "backups": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "status": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "path": PiiClass.NONE,
        "size_bytes": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "db_revision": PiiClass.NONE,
        "suppression_count": PiiClass.NONE,
        "provenance_count": PiiClass.NONE,
        "error": PiiClass.REDACT,
        "local_deleted_at": PiiClass.NONE,
        # Story 1.17. The off-PC outcome, the copy's file name and times; offpc_error is free
        # text like error.
        "offpc_status": PiiClass.NONE,
        "offpc_name": PiiClass.NONE,
        "offpc_ledger": PiiClass.NONE,
        "offpc_at": PiiClass.NONE,
        "offpc_deleted_at": PiiClass.NONE,
        "offpc_error": PiiClass.REDACT,
    },
    # Story 1.18. The restore test's kind, result, times, the tested backup's id, each check's
    # outcome, the reason code, the copy's revision and counts. detail is built from codes,
    # revisions and counts, but it is free text, so redact like the backups' errors.
    "restore_tests": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "result": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "backup_id": PiiClass.NONE,
        "check_fetch": PiiClass.NONE,
        "check_integrity": PiiClass.NONE,
        "check_revision": PiiClass.NONE,
        "check_suppression": PiiClass.NONE,
        "check_provenance": PiiClass.NONE,
        "reason": PiiClass.NONE,
        "copy_revision": PiiClass.NONE,
        "suppression_count": PiiClass.NONE,
        "provenance_count": PiiClass.NONE,
        "detail": PiiClass.REDACT,
    },
    # recovery_state (Story 1.19): codes, times and file names (never a path); no personal data.
    "recovery_state": {
        "id": PiiClass.NONE,
        "mode": PiiClass.NONE,
        "restored_at": PiiClass.NONE,
        "backup_at": PiiClass.NONE,
        "source_name": PiiClass.NONE,
        "pre_restore_name": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
        "reapply_erasure_ledger": PiiClass.NONE,
        "mark_oms_unknown": PiiClass.NONE,
        "reingest_mailboxes": PiiClass.NONE,
        "repoll_unsubscribes": PiiClass.NONE,
    },
    # Story 1.12. Ops alert text carries IDs and counts only, but later kinds (Hot Lead,
    # LinkedIn) are free text that may name a person; every other column is an ID, a code or a
    # time.
    "alerts": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "channel": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "status": PiiClass.NONE,
        "provider_message_id": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "resent_at": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "notifications": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "created_at": PiiClass.NONE,
        "dismissed_at": PiiClass.NONE,
    },
    # Story 1.13. Codes, counts, flags and times only: nextron.domain.budget refuses any value
    # that could hold free text or an address.
    "api_usage": {
        "id": PiiClass.NONE,
        "provider": PiiClass.NONE,
        "operation": PiiClass.NONE,
        "sku": PiiClass.NONE,
        "units": PiiClass.NONE,
        "ok": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
    "ai_usage": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "route_tuple": PiiClass.NONE,
        "served_model": PiiClass.NONE,
        "input_tokens": PiiClass.NONE,
        "output_tokens": PiiClass.NONE,
        "cache_read_tokens": PiiClass.NONE,
        "cache_write_tokens": PiiClass.NONE,
        "iterations": PiiClass.NONE,
        "batch": PiiClass.NONE,
        "cost_micros": PiiClass.NONE,
        "refused": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
}

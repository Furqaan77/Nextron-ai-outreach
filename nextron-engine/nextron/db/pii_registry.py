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

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

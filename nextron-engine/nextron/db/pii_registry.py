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
}

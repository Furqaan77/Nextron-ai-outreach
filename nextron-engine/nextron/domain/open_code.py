"""The one-time code that opens the dashboard (AD-13, FR-56).

``nextron open`` stores ``encode(code, issued_at)`` in the keyring entry ``session/open_code``
and opens the browser at ``/enter?t=<code>``. ``check`` decides what the presented code is
worth. The code is 256 bits as 43 base64url characters, and it is valid for ``OPEN_CODE_TTL``
after it was issued, exactly 5 minutes included.

``check`` looks at, in this order:

1. no stored entry: ``absent``;
2. a stored entry that isn't exactly ``{"code": <43 base64url characters>, "issued_at":
   "YYYY-MM-DDTHH:MM:SS.ffffffZ"}``: ``malformed``;
3. a code issued in the future, or more than ``OPEN_CODE_TTL`` ago: ``expired``;
4. otherwise a constant-time comparison of the UTF-8 bytes: ``accepted`` or ``mismatch``.

``unavailable`` is never returned here: the app service reports it when Credential Manager
fails. A naive ``now`` or ``issued_at`` raises ``ValueError``.
"""

import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

OPEN_CODE_TTL: Final = timedelta(minutes=5)
CODE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{43}")  # matched with fullmatch
TIME_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", re.ASCII)
TIME_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
FIELDS: Final = frozenset({"code", "issued_at"})


class CodeCheck(StrEnum):
    """The outcome of presenting a one-time code."""

    ACCEPTED = "accepted"
    ABSENT = "absent"
    MALFORMED = "malformed"
    EXPIRED = "expired"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def encode(code: str, issued_at: datetime) -> str:
    """The stored form of ``code``: compact JSON with its UTC issue time.

    Raises ``ValueError`` when ``code`` isn't 43 base64url characters or ``issued_at`` is naive.
    """
    _require_aware(issued_at, "issued_at")
    if not isinstance(code, str) or CODE_PATTERN.fullmatch(code) is None:
        raise ValueError("a one-time code is 43 base64url characters")
    stamp = issued_at.astimezone(UTC).strftime(TIME_FORMAT)
    return json.dumps({"code": code, "issued_at": stamp}, separators=(",", ":"))


def _decode(stored: str) -> tuple[str, datetime] | None:
    """The code and issue time in ``stored``, or ``None`` when it is malformed."""
    try:
        record = json.loads(stored)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(record, dict) or set(record) != FIELDS:
        return None
    code, stamp = record["code"], record["issued_at"]
    if not (isinstance(code, str) and isinstance(stamp, str)):
        return None
    if CODE_PATTERN.fullmatch(code) is None or TIME_PATTERN.fullmatch(stamp) is None:
        return None
    try:
        issued_at = datetime.strptime(stamp, TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return code, issued_at


def check(stored: str | None, presented: str, now: datetime) -> CodeCheck:
    """What ``presented`` is worth against the ``stored`` entry at ``now``."""
    _require_aware(now, "now")
    if stored is None:
        return CodeCheck.ABSENT
    decoded = _decode(stored)
    if decoded is None:
        return CodeCheck.MALFORMED
    code, issued_at = decoded
    if now < issued_at or now - issued_at > OPEN_CODE_TTL:
        return CodeCheck.EXPIRED
    if hmac.compare_digest(
        code.encode("utf-8", errors="replace"), presented.encode("utf-8", errors="replace")
    ):
        return CodeCheck.ACCEPTED
    return CodeCheck.MISMATCH

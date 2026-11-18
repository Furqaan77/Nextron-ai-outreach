"""Unsubscribe requests polled from the Worker (FR-70, FR-41, FR-50, AD-10, AD-15, AD-16;
Story 3.11): the pending list's format, the poll period, the Worker host rule and what applying
one request did.

Import rule: the standard library only.

- ``PENDING_FORMAT`` (``nextron-unsub-pending/1``): the ``format`` field of the Worker's
  ``GET /pending`` answer (Story 3.10). Any other value is a bad response.
- ``POLL_PERIOD_PROD`` (5 minutes) and ``POLL_PERIOD_OTHER`` (30 minutes); ``poll_period(env)``
  is the first for exactly ``"prod"`` and the second for any other environment.
- ``is_worker_host(text)``: whether ``text`` may be the ``unsub_worker_host`` setting. It is
  true for blank (polling off) and for a lowercase ASCII DNS name of at most 253 characters,
  with at least 3 labels of 1 to 63 letters, digits or inner hyphens, whose first label is
  exactly ``u`` (the ``u.<Sending Domain>`` route) and which isn't ``workers.dev`` or under it.
  A port, a path, a scheme, a trailing dot, an uppercase letter or any other character fails.
- ``UnsubOutcome``: what ``Compliance.unsubscribe_by_token`` did with one token: ``applied``,
  ``duplicate`` (its ``token_hash`` was recorded already) or ``invalid`` (it didn't decode).
- ``POLL_UNSUBSCRIBES_JOB`` is the job type's name, the same as its AD-10 trigger's.
"""

import re
from datetime import timedelta
from enum import StrEnum
from typing import Final

PENDING_FORMAT: Final = "nextron-unsub-pending/1"
POLL_UNSUBSCRIBES_JOB: Final = "poll_unsubscribes"
PROD: Final = "prod"
POLL_PERIOD_PROD: Final = timedelta(minutes=5)
POLL_PERIOD_OTHER: Final = timedelta(minutes=30)
MAX_HOST_LENGTH: Final = 253
MIN_HOST_LABELS: Final = 3
HOST_PREFIX_LABEL: Final = "u"
REFUSED_PARENT: Final = ("workers", "dev")
LABEL_PATTERN: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


class UnsubOutcome(StrEnum):
    """What applying one unsubscribe token did."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


def poll_period(env: str) -> timedelta:
    """How often the engine polls the Worker in ``env``: 5 minutes in ``prod``, else 30."""
    return POLL_PERIOD_PROD if env == PROD else POLL_PERIOD_OTHER


def is_worker_host(text: object) -> bool:
    """Whether ``text`` is blank or a valid Worker host; see the module docstring."""
    if not isinstance(text, str):
        return False
    if text == "":
        return True
    if len(text) > MAX_HOST_LENGTH:
        return False
    labels = text.split(".")
    if len(labels) < MIN_HOST_LABELS or labels[0] != HOST_PREFIX_LABEL:
        return False
    if not all(LABEL_PATTERN.fullmatch(label) for label in labels):
        return False
    return tuple(labels[-len(REFUSED_PARENT) :]) != REFUSED_PARENT

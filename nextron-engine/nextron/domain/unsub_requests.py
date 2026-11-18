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

Monitoring the poll (FR-70, FR-58, AD-15, AD-20, NFR-32; Story 3.12):

- ``FAILING_AFTER`` (24 hours): how long polling may fail before it is a health issue and an
  alert. ``polling_failing(last_success_at, failing_since, now)`` is true when
  ``failing_since`` is set and ``now - (last_success_at or failing_since)`` is over it.
- ``KV_LIST_DAILY_LIMIT`` (1,000) and ``KV_LIST_ALERT_PCT`` (80): Cloudflare KV's daily list
  limit on the free plan and the share of it that raises the dashboard notice;
  ``kv_list_warning(count)`` is whether ``count`` is at that share or above. ``KV_PROVIDER``
  (``unsub_worker``) and ``KV_LIST_OPERATION`` (``kv_list``) are the usage meter's provider and
  operation of one Worker request.
- ``DOCTOR_DEADLINE_S`` (10.0): how long ``nextron doctor``'s ``unsub_worker`` check waits for
  the Worker.
- ``PollFailure``: the closed codes a failed poll records: the five ``UNSUB_*`` port codes,
  ``unsub_unreachable`` (the doctor probe's slow answer) and ``internal_error``.
  ``failure_code(code)`` maps an ``AdapterError``'s code to one: a closed code is itself,
  ``credential_not_set`` (no bearer stored) is ``unsub_bearer_invalid``, and anything else is
  ``internal_error``.
- ``POLL_FAILING`` (``unsub_poll_failing``) and ``KV_LIST_QUOTA`` (``kv_list_quota``): the
  alert kinds; ``failing_subject(since)`` is ``unsub_poll_failing:<since, epoch seconds>`` (one
  alert per failure episode) and ``kv_list_subject(now)`` ``kv_list:<YYYY-MM-DD>`` (one a UTC
  day).
- The texts hold only times, codes and counts: ``failing_text(since)`` is "Unsubscribe polling
  failing since <YYYY-MM-DD HH:MM UTC>", ``failing_alert_text(since, code)`` adds " (<code>)",
  and ``kv_list_text(count)`` is "Unsubscribe Worker KV list calls today: <count> of 1,000".
"""

import re
from datetime import UTC, datetime, timedelta
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

FAILING_AFTER: Final = timedelta(hours=24)
KV_LIST_DAILY_LIMIT: Final = 1000
KV_LIST_ALERT_PCT: Final = 80
DOCTOR_DEADLINE_S: Final = 10.0
KV_PROVIDER: Final = "unsub_worker"
KV_LIST_OPERATION: Final = "kv_list"
POLL_FAILING: Final = "unsub_poll_failing"
KV_LIST_QUOTA: Final = "kv_list_quota"
CREDENTIAL_NOT_SET: Final = "credential_not_set"  # nextron.ports.secrets.CREDENTIAL_NOT_SET


class UnsubOutcome(StrEnum):
    """What applying one unsubscribe token did."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


class PollFailure(StrEnum):
    """The closed codes of a failed poll (``unsub_poll_state.last_failure_code``)."""

    REFUSED = "unsub_refused"
    BEARER_INVALID = "unsub_bearer_invalid"
    UNAUTHORIZED = "unsub_unauthorized"
    UNAVAILABLE = "unsub_unavailable"
    BAD_RESPONSE = "unsub_bad_response"
    UNREACHABLE = "unsub_unreachable"
    INTERNAL_ERROR = "internal_error"


def failure_code(code: object) -> PollFailure:
    """The closed code recorded for a failure whose error code is ``code``: a closed code is
    itself, ``credential_not_set`` (no bearer stored) is ``unsub_bearer_invalid``, and anything
    else is ``internal_error``."""
    if code == CREDENTIAL_NOT_SET:
        return PollFailure.BEARER_INVALID
    for failure in PollFailure:
        if code == failure.value:
            return failure
    return PollFailure.INTERNAL_ERROR


def polling_failing(
    last_success_at: datetime | None, failing_since: datetime | None, now: datetime
) -> bool:
    """Whether polling has failed for over ``FAILING_AFTER``: ``failing_since`` is set and
    ``now`` is more than that past the last success (or, with none, the first failure)."""
    if failing_since is None:
        return False
    since = last_success_at if last_success_at is not None else failing_since
    return now - since > FAILING_AFTER


def kv_list_warning(count: int) -> bool:
    """Whether ``count`` KV list calls in a day reach ``KV_LIST_ALERT_PCT`` of the limit."""
    return count * 100 >= KV_LIST_DAILY_LIMIT * KV_LIST_ALERT_PCT


def _minute(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def failing_text(since: datetime) -> str:
    """The health issue's text: when the failures began, nothing else."""
    return f"Unsubscribe polling failing since {_minute(since)}"


def failing_alert_text(since: datetime, code: str) -> str:
    """The ``unsub_poll_failing`` alert's text: when the failures began and the last code."""
    return f"{failing_text(since)} ({code})"


def kv_list_text(count: int) -> str:
    """Today's KV list calls against the daily limit."""
    return f"Unsubscribe Worker KV list calls today: {count:,} of {KV_LIST_DAILY_LIMIT:,}"


def failing_subject(since: datetime) -> str:
    """The ``unsub_poll_failing`` alert's subject: one per failure episode."""
    return f"{POLL_FAILING}:{int(since.timestamp())}"


def kv_list_subject(now: datetime) -> str:
    """The ``kv_list_quota`` notice's subject: one per UTC day."""
    return f"kv_list:{now.astimezone(UTC).date().isoformat()}"


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

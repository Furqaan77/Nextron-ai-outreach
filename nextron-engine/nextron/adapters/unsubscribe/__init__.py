"""The unsubscribe Worker adapter (FR-70, AD-9, AD-10, AD-15; Story 3.11): the pending list of
Story 3.10's Cloudflare Worker, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``WorkerUnsubscribe(secrets, *, meter, http=None)``** implements ``UnsubscribePort``. Without
``http`` it builds ``default_http_client()`` (the live constructor: an ``httpx2.Client`` with a
10 s connect timeout and 15 s for each other phase, that never follows redirects) on its first
request, looked up when it is called (so a test can refuse it) and under a lock.

``fetch_pending(host)``, in order:

1. refuses a ``host`` that is blank or fails ``nextron.domain.unsub_requests.is_worker_host``
   (``unsub_refused``, not retryable), with no request;
2. reads the bearer with ``read_credential(secrets, "unsubscribe_bearer")``, every time, and
   never keeps it; its ``AdapterError``s (``credential_not_set``, ``keyring_failed``) propagate
   with no request. A bearer shorter than 43 characters, or holding anything but visible ASCII
   (which can't go in a header), is ``unsub_bearer_invalid`` (not retryable), with no request;
3. makes one GET to ``https://<host>/pending`` with ``Authorization: Bearer <bearer>`` and
   ``Accept: application/json``, never following a redirect, inside ``Metered(meter,
   "unsub_worker", "kv_list")``: exactly one ``ApiCall`` per request, ``ok`` when it returns,
   else the ``AdapterError``'s code.

==============================================  ========================================
Answer                                          Outcome
==============================================  ========================================
2xx, a JSON object of the right shape           the ``PendingFetch``
401 or 403                                      ``unsub_unauthorized``
429, 5xx (a KV failure), any transport failure  retryable ``unsub_unavailable``
any other status (a 3xx included)               ``unsub_refused``, naming the status
a body over 10 MB, not a JSON object, a         ``unsub_bad_response``
``format`` other than ``PENDING_FORMAT``, or
``entries`` that isn't a list
==============================================  ========================================

Each item of ``entries`` that isn't an object, or lacks a ``str`` ``token`` or a ``received_at``
in ISO 8601 with an offset, is counted in ``malformed`` and skipped; ``expiration`` (KV's) is
ignored. Every error is an ``AdapterError`` whose message is this module's own text, raised
``from None``: none carries the bearer, a token, the host or a body, in its message, ``repr`` or
traceback.

**``probe(host, *, deadline_s)``** (Story 3.12, ``nextron doctor``'s ``unsub_worker`` check)
runs steps 1 to 3 the same way (the same refusals, headers and one metered ``kv_list`` call),
with ``deadline_s`` as the request's httpx timeout, and maps the answer by the same table. It
measures the request on a monotonic clock (``monotonic``, ``time.monotonic`` unless injected):
a good answer that took longer than ``deadline_s``, or an httpx timeout, raises
``unsub_unreachable`` (retryable).
Otherwise it returns ``None``; the entries are read and dropped.
"""

import json
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

import httpx2

from nextron.domain.credentials import CredentialKey
from nextron.domain.unsub_requests import (
    KV_LIST_OPERATION,
    KV_PROVIDER,
    PENDING_FORMAT,
    is_worker_host,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.unsubscribe import (
    UNSUB_BAD_RESPONSE,
    UNSUB_BEARER_INVALID,
    UNSUB_REFUSED,
    UNSUB_UNAUTHORIZED,
    UNSUB_UNAVAILABLE,
    UNSUB_UNREACHABLE,
    PendingFetch,
    PendingUnsub,
)
from nextron.ports.usage_meter import Metered, UsageMeterPort

TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0
PROVIDER: Final = KV_PROVIDER  # the usage meter's provider and operation
KV_LIST: Final = KV_LIST_OPERATION
PENDING_PATH: Final = "/pending"
MIN_BEARER_LENGTH: Final = 43  # 32 random bytes as base64url
MAX_BODY_BYTES: Final = 10_000_000


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _bearer_ok(value: str) -> bool:
    """Whether ``value`` can be the bearer: at least 43 visible ASCII characters."""
    return len(value) >= MIN_BEARER_LENGTH and all("!" <= ch <= "~" for ch in value)


class WorkerUnsubscribe:
    """``UnsubscribePort`` over the unsubscribe Worker; see the module docstring."""

    def __init__(
        self,
        secrets: SecretsPort,
        *,
        meter: UsageMeterPort,
        http: httpx2.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._secrets = secrets
        self._meter = meter
        self._http = http
        self._monotonic = monotonic
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def _headers(self, host: str) -> dict[str, str]:
        """Steps 1 and 2: refuse a bad host, then read and check the bearer."""
        if not isinstance(host, str) or host == "" or not is_worker_host(host):
            raise AdapterError(
                False, UNSUB_REFUSED, "the unsubscribe Worker host isn't a valid u. host"
            ) from None
        bearer = read_credential(self._secrets, CredentialKey.UNSUBSCRIBE_BEARER)
        if not _bearer_ok(bearer):
            raise AdapterError(
                False,
                UNSUB_BEARER_INVALID,
                "the unsubscribe Worker bearer secret must be at least 43 visible characters",
            ) from None
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}

    def _get(
        self,
        http: httpx2.Client,
        host: str,
        headers: Mapping[str, str],
        timeout: float | None = None,
    ) -> httpx2.Response:
        """Step 3's one GET; ``timeout`` (seconds) replaces the client's own when given, and
        then an httpx timeout is ``unsub_unreachable`` (the probe's deadline passed)."""
        try:
            return http.get(
                f"https://{host}{PENDING_PATH}",
                headers=dict(headers),
                follow_redirects=False,
                timeout=httpx2.USE_CLIENT_DEFAULT if timeout is None else timeout,
            )
        except httpx2.TimeoutException:
            if timeout is None:
                raise AdapterError(
                    True, UNSUB_UNAVAILABLE, "the unsubscribe Worker request failed"
                ) from None
            raise AdapterError(
                True,
                UNSUB_UNREACHABLE,
                f"the unsubscribe Worker did not answer within {timeout:g} s",
            ) from None
        except Exception:  # noqa: BLE001 - any transport failure: a GET is safe to retry
            raise AdapterError(
                True, UNSUB_UNAVAILABLE, "the unsubscribe Worker request failed"
            ) from None

    def fetch_pending(self, host: str) -> PendingFetch:
        headers = self._headers(host)
        http = self._client()
        with Metered(self._meter, PROVIDER, KV_LIST):  # one ApiCall, whatever the outcome
            return _pending(_answer(self._get(http, host, headers)))

    def probe(self, host: str, *, deadline_s: float) -> None:
        headers = self._headers(host)
        http = self._client()
        with Metered(self._meter, PROVIDER, KV_LIST):  # one ApiCall, whatever the outcome
            started = self._monotonic()
            response = self._get(http, host, headers, deadline_s)
            _pending(_answer(response))
            if self._monotonic() - started > deadline_s:
                raise AdapterError(
                    True,
                    UNSUB_UNREACHABLE,
                    f"the unsubscribe Worker answered, but slower than {deadline_s:g} s",
                ) from None


def _answer(response: httpx2.Response) -> Mapping[str, Any]:
    """The answer's JSON object; an ``AdapterError`` for any other answer."""
    status = response.status_code
    if status in (401, 403):
        raise AdapterError(
            False,
            UNSUB_UNAUTHORIZED,
            f"the unsubscribe Worker refused the bearer secret with HTTP {status}",
        ) from None
    if status == 429 or 500 <= status < 600:
        raise AdapterError(
            True, UNSUB_UNAVAILABLE, f"the unsubscribe Worker answered with HTTP {status}"
        ) from None
    if not 200 <= status < 300:
        raise AdapterError(
            False, UNSUB_REFUSED, f"the unsubscribe Worker refused the request with HTTP {status}"
        ) from None
    try:
        body: bytes | None = response.content
    except Exception:  # noqa: BLE001 - a body that can't be read is a bad response
        body = None
    if body is None or len(body) > MAX_BODY_BYTES:
        raise AdapterError(
            False, UNSUB_BAD_RESPONSE, "the unsubscribe Worker answered with an unreadable body"
        ) from None
    try:
        document = json.loads(body)
    except Exception:  # noqa: BLE001 - any body that isn't JSON is a bad response
        document = None
    if not isinstance(document, dict):
        raise AdapterError(
            False,
            UNSUB_BAD_RESPONSE,
            "the unsubscribe Worker answered with a body that isn't a JSON object",
        ) from None
    return document


def _pending(document: Mapping[str, Any]) -> PendingFetch:
    """The well-formed entries of a pending list, and how many were malformed."""
    if document.get("format") != PENDING_FORMAT:
        raise AdapterError(
            False,
            UNSUB_BAD_RESPONSE,
            "the unsubscribe Worker answered in an unknown format",
        ) from None
    items = document.get("entries")
    if not isinstance(items, list):
        raise AdapterError(
            False,
            UNSUB_BAD_RESPONSE,
            "the unsubscribe Worker answered a list whose entries aren't a list",
        ) from None
    entries: list[PendingUnsub] = []
    malformed = 0
    for item in items:
        found = _entry(item)
        if found is None:
            malformed += 1
        else:
            entries.append(found)
    return PendingFetch(tuple(entries), malformed)


def _entry(item: object) -> PendingUnsub | None:
    """One pending entry, or ``None`` when it is malformed."""
    if not isinstance(item, Mapping):
        return None
    token = item.get("token")
    received = item.get("received_at")
    if not isinstance(token, str) or not isinstance(received, str):
        return None
    try:
        at = datetime.fromisoformat(received)
    except ValueError:
        return None
    if at.tzinfo is None or at.utcoffset() is None:
        return None
    try:
        return PendingUnsub(token, at)
    except (TypeError, ValueError, OverflowError):  # an offset that leaves the calendar
        return None

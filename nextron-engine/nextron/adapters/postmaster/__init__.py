"""The Google Postmaster Tools adapter (FR-38, FR-58, AD-9; Story 6.6): a Sending Domain's
daily user-reported spam rate, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. It is
the only module that names the Postmaster Tools API. Nothing here logs.

**``GooglePostmaster(*, token, meter, http=None)``** implements ``PostmasterPort``. ``token`` is
a ``Callable[[], str]`` (``nextron.main`` passes ``GoogleAccess.access_token("postmaster")``),
called on every request, never kept. Without ``http`` it builds ``default_http_client()`` (the
live constructor: an ``httpx2.Client`` with a 10 s connect timeout and 15 s for each other
phase, that never follows redirects) on its first request, looked up then (so a test can refuse
it) and under a lock.

**``spam_rate(domain, day)``:**

1. ``domain`` must be a DNS name (``nextron.ports.dns.is_dns_name``) and ``day`` a ``date``
   (not a ``datetime``); anything else is ``postmaster_rejected``, with no request.
2. ``token()`` is called; its errors (``google_not_granted``, ``google_needs_reconsent``,
   ``google_client_not_imported``, a Credential Manager failure) propagate, with no request.
3. Inside ``Metered(meter, "postmaster", "traffic_stats")`` (exactly one ``ApiCall`` per
   request, whatever the outcome) it GETs
   ``https://gmailpostmastertools.googleapis.com/v1/domains/<domain>/trafficStats/<YYYYMMDD>``
   with ``Authorization: Bearer <token>``.

=====================================================  =========================================
Result                                                 Outcome
=====================================================  =========================================
2xx, a JSON object with ``userReportedSpamRatio`` a    ``PostmasterReading(ratio_ppm(ratio))``
number from 0 to 1
2xx, a JSON object without ``userReportedSpamRatio``   ``PostmasterReading(None)`` (no data)
any other 2xx                                          ``postmaster_bad_response``
403 (domain not verified) or 404 (no stats that day)   ``PostmasterReading(None)`` (no data)
429 or 5xx                                             retryable ``postmaster_unavailable``
any other status (400, 401, a 3xx, ...)                ``postmaster_rejected``, naming the status
any exception from the request (a GET is safe to       retryable ``postmaster_unavailable``
retry)
=====================================================  =========================================

Every error is an ``AdapterError`` with this module's own text, raised ``from None``: none
carries the token, the domain or a body.
"""

import json
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Final

import httpx2

from nextron.domain.reputation import ratio_ppm
from nextron.ports.dns import is_dns_name
from nextron.ports.errors import AdapterError
from nextron.ports.postmaster import (
    POSTMASTER_BAD_RESPONSE,
    POSTMASTER_REJECTED,
    POSTMASTER_UNAVAILABLE,
    PostmasterReading,
)
from nextron.ports.usage_meter import Metered, UsageMeterPort

API_BASE: Final = "https://gmailpostmastertools.googleapis.com/v1"
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0
PROVIDER: Final = "postmaster"  # the usage meter's provider and operation
TRAFFIC_STATS: Final = "traffic_stats"
RATIO_FIELD: Final = "userReportedSpamRatio"
NO_DATA_STATUSES: Final = frozenset({403, 404})
MAX_BODY_BYTES: Final = 1_000_000


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _checked(domain: object, day: object) -> tuple[str, date]:
    """Step 1: the domain (lower-case, no final dot) and the day; ``postmaster_rejected``
    otherwise."""
    try:
        valid = isinstance(domain, str) and is_dns_name(domain)
    except TypeError:
        valid = False
    if not valid:
        raise AdapterError(False, POSTMASTER_REJECTED, "the domain is not a DNS name") from None
    if not isinstance(day, date) or isinstance(day, datetime):
        raise AdapterError(False, POSTMASTER_REJECTED, "the day is not a date") from None
    return str(domain).lower().removesuffix("."), day


class GooglePostmaster:
    """``PostmasterPort`` over the Postmaster Tools API; see the module docstring."""

    def __init__(
        self,
        *,
        token: Callable[[], str],
        meter: UsageMeterPort,
        http: httpx2.Client | None = None,
    ) -> None:
        self._token = token
        self._meter = meter
        self._http = http
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def spam_rate(self, domain: str, day: date) -> PostmasterReading:
        name, checked_day = _checked(domain, day)
        token = self._token()  # step 2: read at call time, never kept
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"{API_BASE}/domains/{name}/trafficStats/{checked_day:%Y%m%d}"
        http = self._client()
        with Metered(self._meter, PROVIDER, TRAFFIC_STATS):  # one ApiCall, whatever the outcome
            try:
                response = http.get(url, headers=headers, follow_redirects=False)
            except Exception:  # noqa: BLE001 - any transport failure: a GET is safe to retry
                raise AdapterError(
                    True, POSTMASTER_UNAVAILABLE, "the Postmaster Tools request failed"
                ) from None
            return _reading(response)


def _reading(response: httpx2.Response) -> PostmasterReading:
    status = response.status_code
    if status in NO_DATA_STATUSES:
        return PostmasterReading(None)
    if status == 429 or 500 <= status < 600:
        raise AdapterError(
            True, POSTMASTER_UNAVAILABLE, f"Postmaster Tools answered with HTTP {status}"
        ) from None
    if not 200 <= status < 300:
        raise AdapterError(
            False, POSTMASTER_REJECTED, f"Postmaster Tools refused the request with HTTP {status}"
        ) from None
    document = _document(response)
    if RATIO_FIELD not in document:
        return PostmasterReading(None)
    ratio = document[RATIO_FIELD]
    if isinstance(ratio, bool) or not isinstance(ratio, int | float):
        raise AdapterError(
            False,
            POSTMASTER_BAD_RESPONSE,
            "Postmaster Tools answered a spam ratio that isn't a number",
        ) from None
    try:
        return PostmasterReading(ratio_ppm(ratio))
    except ValueError:
        raise AdapterError(
            False, POSTMASTER_BAD_RESPONSE, "Postmaster Tools answered a spam ratio outside 0 to 1"
        ) from None


def _document(response: httpx2.Response) -> dict[str, Any]:
    """The answer's JSON object; ``postmaster_bad_response`` for anything else."""
    try:
        body: bytes | None = response.content
    except Exception:  # noqa: BLE001 - a body that can't be read is a bad response
        body = None
    if body is None or len(body) > MAX_BODY_BYTES:
        raise AdapterError(
            False, POSTMASTER_BAD_RESPONSE, "Postmaster Tools answered with an unreadable body"
        ) from None
    try:
        document = json.loads(body)
    except Exception:  # noqa: BLE001 - any body that isn't JSON is a bad response
        document = None
    if not isinstance(document, dict):
        raise AdapterError(
            False,
            POSTMASTER_BAD_RESPONSE,
            "Postmaster Tools answered with a body that isn't a JSON object",
        ) from None
    return document

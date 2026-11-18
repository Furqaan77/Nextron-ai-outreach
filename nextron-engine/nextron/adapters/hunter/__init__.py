"""The Hunter adapter (AD-9, FR-14, FR-15, FR-57, INT-6, Stories 2.13 and 2.14): Hunter's Domain
Search, Email Finder and Email Verifier, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``HunterFinder(secrets, *, meter, http=None)``** implements ``EmailFinderPort``,
``DomainSearchPort`` and ``EmailVerifierPort`` (``nextron.ports.email_finder``). Without
``http`` it builds ``default_http_client()`` (the live constructor: an ``httpx2.Client`` with a
10 s connect timeout and 15 s for each other phase, that never follows redirects) on its first
request, looked up when it is called (so a test can refuse it) and under a lock.

**Each call** reads the API key with ``read_credential(secrets, "hunter")`` and never keeps it;
its ``AdapterError``s (``credential_not_set``, ``keyring_failed``) propagate before any request
or client is built. The key goes only in the ``X-API-KEY`` header, never in a URL. Each call
makes one GET under ``API_BASE`` (``https://api.hunter.io/v2/``):

- ``domain_search(domain)``: ``domain-search?domain=<domain>&limit=10``. A 2xx gives
  ``DomainSearchAnswer(data.pattern, people)``, one ``DomainPerson`` per ``data.emails[]``
  entry (``value``; ``type == "personal"``; ``first_name`` and ``last_name``, ``None`` unless a
  ``str``). An entry that isn't an object, or whose ``value`` isn't a non-blank ``str``, is
  skipped; a missing ``emails`` is none. A 2xx that isn't a JSON object with an object
  ``data``, whose ``pattern`` is a ``str`` or ``null`` and whose ``emails`` (when present) is a
  list, is ``hunter_bad_answer``.
- ``find_email(query)``: ``email-finder?domain=&first_name=&last_name=``. A 2xx gives
  ``FinderAnswer(data.email)`` for a non-blank ``str``, and ``FinderAnswer(None)`` for ``null``
  or a blank one; any other shape is ``hunter_bad_answer``. A 404 is ``FinderAnswer(None)``, and
  a 451 (Hunter's ``claimed_email``: the person asked Hunter not to process their address) is
  ``FinderAnswer(None, claimed=True)``.
- ``verify_email(email)`` (Story 2.14): ``email-verifier?email=<email>``. A 200 gives
  ``VerifierAnswer(data.status)`` for a non-blank ``str`` status, any other shape is
  ``hunter_bad_answer``; a 222 (Hunter couldn't verify the address) is
  ``VerifierAnswer("unknown")`` whatever its body; a 202 (the verification is still running)
  is a retryable ``hunter_unavailable`` and isn't counted.

=====================================================  =========================================
Other results (every call)                             Outcome
=====================================================  =========================================
any exception from the request (no answer came back)   retryable ``hunter_unavailable``
403 (Hunter's rate limit), 429 or 5xx                  retryable ``hunter_unavailable``
401, any other 4xx, a 1xx or a 3xx                     ``hunter_refused``, naming the status
=====================================================  =========================================

**Metering (FR-57).** Every request is recorded through ``meter`` (``UsageMeterPort``, through
``Metered``) as exactly one ``ApiCall("hunter", "domain_search" | "find" | "verify")``, ``ok``
or with the ``AdapterError``'s code, recorded before the error propagates. ``count`` is 1 for a
2xx answer (for ``verify``, a 200 or a 222) and 0 otherwise: a 2xx counts in full even when
nothing is found or the body is bad, so the quota errs low.
``nextron.domain.budget.UNIT_WEIGHTS`` doubles find and Domain Search, so a 2xx stores 2 units;
a verification stores 1. A call refused before any request records nothing. Hunter's
``X-Credits-Used`` header is ignored.

Every error is an ``AdapterError`` whose message is this module's own text, raised ``from
None``: none carries the key, the URL, the domain, a name, an address or a response body, in
its message, ``repr`` or traceback.
"""

import threading
from collections.abc import Mapping
from typing import Final, NoReturn

import httpx2

from nextron.domain.credentials import CredentialKey
from nextron.ports.email_finder import (
    DomainPerson,
    DomainSearchAnswer,
    FinderAnswer,
    FinderQuery,
    VerifierAnswer,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.usage_meter import Metered, UsageMeterPort

API_BASE: Final = "https://api.hunter.io/v2/"
DOMAIN_SEARCH_PATH: Final = "domain-search"
EMAIL_FINDER_PATH: Final = "email-finder"
EMAIL_VERIFIER_PATH: Final = "email-verifier"
API_KEY_HEADER: Final = "X-API-KEY"
DOMAIN_SEARCH_LIMIT: Final = 10
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0

HUNTER_UNAVAILABLE: Final = "hunter_unavailable"
HUNTER_REFUSED: Final = "hunter_refused"
HUNTER_BAD_ANSWER: Final = "hunter_bad_answer"
PROVIDER: Final = "hunter"  # the usage meter's provider and operations
DOMAIN_SEARCH: Final = "domain_search"
FIND: Final = "find"
VERIFY: Final = "verify"

NOT_FOUND: Final = 404  # Email Finder: nobody found
CLAIMED: Final = 451  # Email Finder: claimed_email, the person opted out of Hunter
VERIFYING: Final = 202  # Email Verifier: the verification is still running
UNVERIFIABLE: Final = 222  # Email Verifier: Hunter couldn't verify the address
UNKNOWN_STATUS: Final = "unknown"
RATE_LIMITED: Final = (403, 429)  # Hunter answers 403 when its rate limit is passed
BAD_ANSWER: Final = "Hunter's answer was not the expected JSON"


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _is_2xx(status: int) -> bool:
    return 200 <= status < 300


def _data(response: httpx2.Response) -> dict[str, object]:
    """The ``data`` object of a 2xx answer; ``hunter_bad_answer`` when there is none."""
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - any body that isn't JSON is a bad answer
        document = None
    data = document.get("data") if isinstance(document, dict) else None
    if not isinstance(data, dict):
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    return data


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _person(entry: object) -> DomainPerson | None:
    """One ``data.emails[]`` entry as a ``DomainPerson``, or ``None`` to skip it."""
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if not isinstance(value, str) or not value.strip():
        return None
    return DomainPerson(
        value,
        _optional_str(entry.get("first_name")),
        _optional_str(entry.get("last_name")),
        entry.get("type") == "personal",
    )


def _domain_answer(response: httpx2.Response) -> DomainSearchAnswer:
    data = _data(response)
    pattern = data.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    emails = data.get("emails", [])
    if not isinstance(emails, list):
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    people = tuple(person for entry in emails if (person := _person(entry)) is not None)
    return DomainSearchAnswer(pattern, people)


def _finder_answer(response: httpx2.Response) -> FinderAnswer:
    data = _data(response)
    if "email" not in data:
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    email = data["email"]
    if email is None:
        return FinderAnswer(None)
    if not isinstance(email, str):
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    return FinderAnswer(email if email.strip() else None)


def _verifier_answer(response: httpx2.Response) -> VerifierAnswer:
    status = _data(response).get("status")
    if not isinstance(status, str) or not status.strip():
        raise AdapterError(False, HUNTER_BAD_ANSWER, BAD_ANSWER) from None
    return VerifierAnswer(status)


def _refuse_status(status: int) -> NoReturn:
    """Raise the error for a status that isn't 2xx and that no call reads an answer from."""
    if status in RATE_LIMITED or 500 <= status < 600:
        raise AdapterError(
            True, HUNTER_UNAVAILABLE, f"Hunter answered with HTTP {status}"
        ) from None
    raise AdapterError(
        False, HUNTER_REFUSED, f"Hunter refused the request with HTTP {status}"
    ) from None


class HunterFinder:
    """``EmailFinderPort`` and ``DomainSearchPort`` over Hunter's API; see the module
    docstring."""

    def __init__(
        self,
        secrets: SecretsPort,
        *,
        meter: UsageMeterPort,
        http: httpx2.Client | None = None,
    ) -> None:
        self._secrets = secrets
        self._meter = meter
        self._http = http
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def domain_search(self, domain: str) -> DomainSearchAnswer:
        key = read_credential(self._secrets, CredentialKey.HUNTER)
        http = self._client()
        params = {"domain": domain, "limit": str(DOMAIN_SEARCH_LIMIT)}
        with Metered(self._meter, PROVIDER, DOMAIN_SEARCH, count=0) as metered:
            response = self._get(http, key, DOMAIN_SEARCH_PATH, params)
            status = response.status_code
            if not _is_2xx(status):
                _refuse_status(status)
            metered.count = 1  # a 2xx counts in full, whatever it holds
            return _domain_answer(response)

    def find_email(self, query: FinderQuery) -> FinderAnswer:
        key = read_credential(self._secrets, CredentialKey.HUNTER)
        http = self._client()
        params = {
            "domain": query.domain,
            "first_name": query.first_name,
            "last_name": query.last_name,
        }
        with Metered(self._meter, PROVIDER, FIND, count=0) as metered:
            response = self._get(http, key, EMAIL_FINDER_PATH, params)
            status = response.status_code
            if status == NOT_FOUND:
                return FinderAnswer(None)
            if status == CLAIMED:
                return FinderAnswer(None, claimed=True)
            if not _is_2xx(status):
                _refuse_status(status)
            metered.count = 1  # a 2xx counts in full, whatever it holds
            return _finder_answer(response)

    def verify_email(self, email: str) -> VerifierAnswer:
        key = read_credential(self._secrets, CredentialKey.HUNTER)
        http = self._client()
        with Metered(self._meter, PROVIDER, VERIFY, count=0) as metered:
            response = self._get(http, key, EMAIL_VERIFIER_PATH, {"email": email})
            status = response.status_code
            if status == VERIFYING:  # not finished: retried later, not counted
                raise AdapterError(
                    True, HUNTER_UNAVAILABLE, "Hunter is still verifying the address"
                ) from None
            if status == UNVERIFIABLE:
                metered.count = 1
                return VerifierAnswer(UNKNOWN_STATUS)
            if not _is_2xx(status):
                _refuse_status(status)
            metered.count = 1  # a 2xx counts in full, whatever it holds
            return _verifier_answer(response)

    @staticmethod
    def _get(
        http: httpx2.Client, key: str, path: str, params: Mapping[str, str]
    ) -> httpx2.Response:
        """The one GET; only ``AdapterError`` gets out."""
        try:
            return http.get(f"{API_BASE}{path}", params=params, headers={API_KEY_HEADER: key})
        except Exception:  # noqa: BLE001 - whatever failed, no answer came back
            raise AdapterError(
                True, HUNTER_UNAVAILABLE, "the Hunter request could not be completed"
            ) from None

"""The Prospeo adapter (AD-9, FR-14, FR-57, INT-6, Story 2.13): a person's verified address
through Prospeo's Enrich Person, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``ProspeoFinder(secrets, *, meter, http=None)``** implements ``EmailFinderPort``
(``nextron.ports.email_finder``). Without ``http`` it builds ``default_http_client()`` (the live
constructor: an ``httpx2.Client`` with a 10 s connect timeout and 15 s for each other phase,
that never follows redirects) on its first request, looked up when it is called (so a test can
refuse it) and under a lock. Prospeo's old Email Finder endpoint is no longer documented, so
this uses Enrich Person, asking only for a verified address.

**``find_email(query)``** reads the API key with ``read_credential(secrets, "prospeo")`` and
never keeps it; its ``AdapterError``s (``credential_not_set``, ``keyring_failed``) propagate
before any request or client is built (the key is optional, so the caller skips Prospeo when it
isn't set). The key goes only in the ``X-KEY`` header. It POSTs ``API_URL``
(``https://api.prospeo.io/enrich-person``) with the JSON body ``{"only_verified_email": true,
"data": {"first_name": ..., "last_name": ..., "company_website": <domain>}}``.

=====================================================  =========================================
Result                                                 Outcome
=====================================================  =========================================
``error_code`` ``INSUFFICIENT_CREDITS``, any status    retryable ``prospeo_unavailable``
``error_code`` ``INVALID_API_KEY``, any status         ``prospeo_refused``
2xx, a JSON object with ``"error": false``             ``FinderAnswer(person.email.email)`` when
                                                       that is a non-blank ``str``, else
                                                       ``FinderAnswer(None)``
any other 2xx                                          ``prospeo_bad_answer``
400 with ``error_code`` ``NO_MATCH``                   ``FinderAnswer(None)``
any exception from the request (no answer came back),  retryable ``prospeo_unavailable``
429 or 5xx
any other status (401, 403, 404, another 4xx, a 1xx    ``prospeo_refused``, naming the status
or a 3xx)
=====================================================  =========================================

**Metering (FR-57).** Every request is recorded through ``meter`` (``UsageMeterPort``, through
``Metered``) as exactly one ``ApiCall("prospeo", "find")``, ``ok`` or with the
``AdapterError``'s code, recorded before the error propagates. ``count`` is 1 for a 2xx answer
and 0 otherwise: a 2xx counts in full even when it holds no address or is bad, so the quota
errs low. A stored 2xx is 1 unit. A call refused before any request records nothing.

Every error is an ``AdapterError`` whose message is this module's own text, raised ``from
None``: none carries the key, the URL, the domain, a name, an address or a response body, in
its message, ``repr`` or traceback.
"""

import threading
from typing import Final

import httpx2

from nextron.domain.credentials import CredentialKey
from nextron.ports.email_finder import FinderAnswer, FinderQuery
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.usage_meter import Metered, UsageMeterPort

API_URL: Final = "https://api.prospeo.io/enrich-person"
API_KEY_HEADER: Final = "X-KEY"
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0

PROSPEO_UNAVAILABLE: Final = "prospeo_unavailable"
PROSPEO_REFUSED: Final = "prospeo_refused"
PROSPEO_BAD_ANSWER: Final = "prospeo_bad_answer"
PROVIDER: Final = "prospeo"  # the usage meter's provider and operation
FIND: Final = "find"

# Prospeo's own error codes, read from an answer's ``error_code``.
NO_MATCH: Final = "NO_MATCH"
INSUFFICIENT_CREDITS: Final = "INSUFFICIENT_CREDITS"
INVALID_API_KEY: Final = "INVALID_API_KEY"
NO_MATCH_STATUS: Final = 400
BAD_ANSWER: Final = "Prospeo's answer was not the expected JSON"


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _document(response: httpx2.Response) -> dict[str, object] | None:
    """The answer's JSON object, or ``None`` when its body isn't one."""
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - any body that isn't JSON has no object
        return None
    return document if isinstance(document, dict) else None


def _error_code(document: dict[str, object] | None) -> str | None:
    code = None if document is None else document.get("error_code")
    return code if isinstance(code, str) else None


def _email(document: dict[str, object]) -> str | None:
    """``person.email.email`` when it is a non-blank ``str``, else ``None``."""
    person = document.get("person")
    email = person.get("email") if isinstance(person, dict) else None
    address = email.get("email") if isinstance(email, dict) else None
    return address if isinstance(address, str) and address.strip() else None


def _answer(response: httpx2.Response) -> FinderAnswer:
    """The outcome of one Enrich Person answer; see the module docstring."""
    status = response.status_code
    document = _document(response)
    code = _error_code(document)
    if code == INSUFFICIENT_CREDITS:
        raise AdapterError(
            True, PROSPEO_UNAVAILABLE, "Prospeo has no credits left for the request"
        ) from None
    if code == INVALID_API_KEY:
        raise AdapterError(False, PROSPEO_REFUSED, "Prospeo refused the API key") from None
    if 200 <= status < 300:
        if document is None or document.get("error") is not False:
            raise AdapterError(False, PROSPEO_BAD_ANSWER, BAD_ANSWER) from None
        return FinderAnswer(_email(document))
    if status == NO_MATCH_STATUS and code == NO_MATCH:
        return FinderAnswer(None)
    if status == 429 or 500 <= status < 600:
        raise AdapterError(
            True, PROSPEO_UNAVAILABLE, f"Prospeo answered with HTTP {status}"
        ) from None
    raise AdapterError(
        False, PROSPEO_REFUSED, f"Prospeo refused the request with HTTP {status}"
    ) from None


class ProspeoFinder:
    """``EmailFinderPort`` over Prospeo's Enrich Person; see the module docstring."""

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

    def find_email(self, query: FinderQuery) -> FinderAnswer:
        key = read_credential(self._secrets, CredentialKey.PROSPEO)
        http = self._client()
        body = {
            "only_verified_email": True,
            "data": {
                "first_name": query.first_name,
                "last_name": query.last_name,
                "company_website": query.domain,
            },
        }
        with Metered(self._meter, PROVIDER, FIND, count=0) as metered:
            response = self._post(http, key, body)
            if 200 <= response.status_code < 300:
                metered.count = 1  # a 2xx counts in full, whatever it holds
            return _answer(response)

    @staticmethod
    def _post(http: httpx2.Client, key: str, body: dict[str, object]) -> httpx2.Response:
        """The one POST; only ``AdapterError`` gets out."""
        try:
            return http.post(API_URL, json=body, headers={API_KEY_HEADER: key})
        except Exception:  # noqa: BLE001 - whatever failed, no answer came back
            raise AdapterError(
                True, PROSPEO_UNAVAILABLE, "the Prospeo request could not be completed"
            ) from None

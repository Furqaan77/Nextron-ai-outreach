"""The Companies House adapter (FR-12, FR-57, AD-9, INT-5; Story 2.12): the UK companies register
through the Companies House public data API, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``CompaniesHouseRegistry(secrets, *, meter, http=None, monotonic=time.monotonic)``**
implements ``RegistryPort``. Without ``http`` it builds ``default_http_client()`` (the live
constructor: an ``httpx2.Client`` with a 10 s connect timeout and 15 s for each other phase, that
never follows redirects) on its first call, looked up when it is called (so a test can refuse
it) and under a lock.

Each call, in order:

1. reads the API key with ``read_credential(secrets, "companies_house")``, every time, and never
   keeps it; its ``AdapterError``s (``credential_not_set``, ``keyring_failed``) propagate with no
   request made;
2. reserves a slot in the self rate limit: a sliding window of the last ``RATE_LIMIT`` (600)
   request times, read from ``monotonic``. A request that would be the 601st within
   ``RATE_WINDOW_S`` (300 s) raises the retryable ``registry_rate_limited`` with no request and
   nothing metered. It never sleeps;
3. makes one GET to ``https://api.company-information.service.gov.uk`` with ``Authorization:
   Basic base64("<key>:")`` and ``Accept: application/json``, inside ``Metered(meter,
   "companies_house", <operation>)``: exactly one ``ApiCall`` per request, ``ok`` when the call
   returns (a profile 404 included), else the ``AdapterError``'s code.

- ``search_companies(name)`` (operation ``search_companies``): ``/search/companies`` with
  ``q=<name>`` (whitespace collapsed) and ``items_per_page=20``. Each item of ``items`` is read
  from ``company_number``, ``title``, ``company_type``, ``company_status`` and
  ``address.postal_code``; an item that doesn't make a valid ``RegistryCompany`` is skipped, and
  a body without ``items`` (or with ``items: null``) has no hits.
- ``company(number)`` (operation ``get_company``): ``/company/<number>``, read from
  ``company_number``, ``company_name``, ``type``, ``company_status``,
  ``registered_office_address.postal_code``, ``accounts.last_accounts.type`` (the accounts
  category, Story 8.4) and ``sic_codes``; a 404 is ``None``.
- ``search(keyword, sic_codes, page)`` (operation ``search_registry``, Story 8.4):
  ``/advanced-search/companies`` with ``company_status=active``,
  ``company_name_includes=<keyword>`` (whitespace collapsed; left out when ``keyword`` is
  ``None``), ``sic_codes=<codes joined by ,>`` (left out when empty), ``size=20`` and
  ``start_index=<page * 20>``. Each item of ``items`` is read like a profile
  (``company_number``, ``company_name``, ``company_type``, ``company_status``,
  ``registered_office_address.postal_code`` and ``sic_codes``; a search item has no accounts);
  an invalid item is skipped, and a 404 (the API's answer when nothing matches) or a body
  without ``items`` (or with ``items: null``) has no hits. Neither a keyword nor a SIC code, a blank keyword, a code that
  isn't 5 digits, or a page that isn't an ``int`` from 0 is ``registry_refused`` with no request.
- ``officers(company_number)`` (operation ``list_officers``, Story 8.4):
  ``/company/<number>/officers`` with ``items_per_page=100``. Each item of ``items`` is read
  from ``name``, ``officer_role``, ``occupation`` (whitespace collapsed; ``None`` when missing,
  blank or over 200 characters) and ``resigned_on`` (resigned when it is a non-blank text); an
  item without a valid name or role is skipped. A 404 is ``()``.

A number or a name is read stripped (the number uppercased, the name's whitespace collapsed); an
unknown or invalid type or status, and a postcode that isn't a text of at most 16 characters,
read as ``None``.

==============================================  ========================================
Answer                                          Outcome
==============================================  ========================================
2xx, a JSON object of the right shape           the hits, or the company
404 to ``company``                              ``None``
404 to ``search`` or ``officers`` (Story 8.4)   no hits, no officers (``()``)
429                                             retryable ``registry_rate_limited``
5xx, or any transport failure                   retryable ``registry_unavailable``
401 or 403                                      ``registry_unauthorized``
any other status (a 3xx included)               ``registry_refused``, naming the status
a body over 1 MB, not a JSON object, or a       ``registry_bad_response``
profile without a valid number and name
==============================================  ========================================

A blank name or a number that isn't ``[A-Z0-9]{8}`` is ``registry_refused`` with no request.
Every error is an ``AdapterError`` whose message is this module's own text, raised ``from
None``: none carries the key, the name searched, the URL or a response body, in its message,
``repr`` or traceback.
"""

import base64
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Final

import httpx2

from nextron.domain.credentials import CredentialKey
from nextron.domain.legal_form import (
    COMPANY_NUMBER_PATTERN,
    COMPANY_STATUS_PATTERN,
    LEGAL_FORM_PATTERN,
)
from nextron.ports.errors import AdapterError
from nextron.ports.registry import (
    ACCOUNTS_CATEGORY_PATTERN,
    MAX_NAME,
    MAX_POSTCODE,
    PAGE_SIZE,
    REGISTRY_BAD_RESPONSE,
    REGISTRY_RATE_LIMITED,
    REGISTRY_REFUSED,
    REGISTRY_UNAUTHORIZED,
    REGISTRY_UNAVAILABLE,
    SIC_CODE_PATTERN,
    RegistryCompany,
    RegistryOfficer,
)
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.usage_meter import Metered, UsageMeterPort

API_BASE: Final = "https://api.company-information.service.gov.uk"
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0
PROVIDER: Final = "companies_house"  # the usage meter's provider and operations
SEARCH_COMPANIES: Final = "search_companies"
GET_COMPANY: Final = "get_company"
SEARCH_REGISTRY: Final = "search_registry"  # Story 8.4
LIST_OFFICERS: Final = "list_officers"  # Story 8.4
ITEMS_PER_PAGE: Final = 20
OFFICERS_PER_PAGE: Final = 100
ACTIVE_STATUS: Final = "active"
RATE_LIMIT: Final = 600  # requests ...
RATE_WINDOW_S: Final = 300.0  # ... per 5 minutes (the API's own limit)
MAX_BODY_BYTES: Final = 1_000_000
NOT_FOUND: Final = 404

type Document = Mapping[str, Any]


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _code(value: object, pattern: Any) -> str | None:
    """A type or status code as given, or ``None`` when it isn't a valid one."""
    if isinstance(value, str) and pattern.fullmatch(value) is not None:
        return value
    return None


def _postcode(address: object) -> str | None:
    if not isinstance(address, Mapping):
        return None
    value = address.get("postal_code")
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if 1 <= len(value) <= MAX_POSTCODE else None


def _accounts_category(accounts: object) -> str | None:
    """``accounts.last_accounts.type``, or ``None`` when it isn't a valid code."""
    if not isinstance(accounts, Mapping):
        return None
    last = accounts.get("last_accounts")
    if not isinstance(last, Mapping):
        return None
    return _code(last.get("type"), ACCOUNTS_CATEGORY_PATTERN)


def _sic_codes(value: object) -> tuple[str, ...]:
    """The valid 5-digit codes of a ``sic_codes`` list, each once, in order."""
    if not isinstance(value, list):
        return ()
    codes: list[str] = []
    for code in value:
        if isinstance(code, str):
            code = code.strip()
            if SIC_CODE_PATTERN.fullmatch(code) is not None and code not in codes:
                codes.append(code)
    return tuple(codes)


def _company(
    number: object,
    name: object,
    company_type: object,
    status: object,
    address: object,
    accounts: object = None,
    sic_codes: object = None,
) -> RegistryCompany | None:
    """A ``RegistryCompany`` from an answer's fields, or ``None`` when its number or name isn't
    valid."""
    if not isinstance(number, str) or not isinstance(name, str):
        return None
    number = number.strip().upper()
    name = " ".join(name.split())
    if COMPANY_NUMBER_PATTERN.fullmatch(number) is None or not 1 <= len(name) <= MAX_NAME:
        return None
    try:
        return RegistryCompany(
            number=number,
            name=name,
            company_type=_code(company_type, LEGAL_FORM_PATTERN),
            company_status=_code(status, COMPANY_STATUS_PATTERN),
            office_postcode=_postcode(address),
            accounts_category=_accounts_category(accounts),
            sic_codes=_sic_codes(sic_codes),
        )
    except (TypeError, ValueError):  # the checks above make this unreachable
        return None


def _officer(item: Mapping[str, Any]) -> RegistryOfficer | None:
    """A ``RegistryOfficer`` from an officers item, or ``None`` when its name or role isn't
    valid."""
    name = item.get("name")
    role = item.get("officer_role")
    if not isinstance(name, str) or not isinstance(role, str):
        return None
    name = " ".join(name.split())
    role = role.strip()
    if not 1 <= len(name) <= MAX_NAME or LEGAL_FORM_PATTERN.fullmatch(role) is None:
        return None
    raw = item.get("occupation")
    occupation = " ".join(raw.split()) if isinstance(raw, str) else ""
    resigned_on = item.get("resigned_on")
    try:
        return RegistryOfficer(
            name=name,
            role=role,
            occupation=occupation if 1 <= len(occupation) <= MAX_NAME else None,
            resigned=isinstance(resigned_on, str) and bool(resigned_on.strip()),
        )
    except (TypeError, ValueError):  # the checks above make this unreachable
        return None


class CompaniesHouseRegistry:
    """``RegistryPort`` over the Companies House API; see the module docstring."""

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
        self._window_lock = threading.Lock()
        self._sent: deque[float] = deque()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def _reserve(self) -> None:
        """Take a slot in the rate limit's window, or raise ``registry_rate_limited``."""
        with self._window_lock:
            now = self._monotonic()
            while self._sent and self._sent[0] <= now - RATE_WINDOW_S:
                self._sent.popleft()
            if len(self._sent) >= RATE_LIMIT:
                raise AdapterError(
                    True,
                    REGISTRY_RATE_LIMITED,
                    "the Companies House rate limit (600 requests per 5 minutes) is reached",
                ) from None
            self._sent.append(now)

    def search_companies(self, name: str) -> tuple[RegistryCompany, ...]:
        query = " ".join(name.split()) if isinstance(name, str) else ""
        if not query:
            raise AdapterError(
                False, REGISTRY_REFUSED, "a Companies House search needs a name"
            ) from None
        params = {"q": query, "items_per_page": str(ITEMS_PER_PAGE)}
        hits = self._get(SEARCH_COMPANIES, "/search/companies", params, document_hits)
        return () if hits is None else hits  # only a profile's 404 is None

    def company(self, number: str) -> RegistryCompany | None:
        if not isinstance(number, str) or COMPANY_NUMBER_PATTERN.fullmatch(number) is None:
            raise AdapterError(
                False, REGISTRY_REFUSED, "a Companies House lookup needs a company number"
            ) from None
        return self._get(
            GET_COMPANY, f"/company/{number}", None, document_company, missing_ok=True
        )

    def search(
        self, keyword: str | None, sic_codes: tuple[str, ...], page: int
    ) -> tuple[RegistryCompany, ...]:
        query = None
        if keyword is not None:
            query = " ".join(keyword.split()) if isinstance(keyword, str) else ""
            if not query:
                raise AdapterError(
                    False, REGISTRY_REFUSED, "a Companies House search keyword can't be blank"
                ) from None
        if (
            not isinstance(sic_codes, tuple)
            or not all(
                isinstance(code, str) and SIC_CODE_PATTERN.fullmatch(code) is not None
                for code in sic_codes
            )
            or (query is None and not sic_codes)
        ):
            raise AdapterError(
                False,
                REGISTRY_REFUSED,
                "a Companies House search needs a keyword or 5-digit SIC codes",
            ) from None
        if isinstance(page, bool) or not isinstance(page, int) or page < 0:
            raise AdapterError(
                False, REGISTRY_REFUSED, "a Companies House search page is an int from 0"
            ) from None
        params = {"company_status": ACTIVE_STATUS}
        if query is not None:
            params["company_name_includes"] = query
        if sic_codes:
            params["sic_codes"] = ",".join(sic_codes)
        params["size"] = str(PAGE_SIZE)
        params["start_index"] = str(page * PAGE_SIZE)
        hits = self._get(
            SEARCH_REGISTRY,
            "/advanced-search/companies",
            params,
            document_search_hits,
            missing_ok=True,
        )
        return () if hits is None else hits  # a 404 is the API's "nothing matches"

    def officers(self, company_number: str) -> tuple[RegistryOfficer, ...]:
        if (
            not isinstance(company_number, str)
            or COMPANY_NUMBER_PATTERN.fullmatch(company_number) is None
        ):
            raise AdapterError(
                False, REGISTRY_REFUSED, "a Companies House officers list needs a company number"
            ) from None
        found = self._get(
            LIST_OFFICERS,
            f"/company/{company_number}/officers",
            {"items_per_page": str(OFFICERS_PER_PAGE)},
            document_officers,
            missing_ok=True,
        )
        return () if found is None else found

    def _get[T](
        self,
        operation: str,
        path: str,
        params: Mapping[str, str] | None,
        read: Callable[[Document], T],
        *,
        missing_ok: bool = False,
    ) -> T | None:
        """One metered GET, its JSON object read by ``read`` inside the metered block; ``None``
        for a 404 when ``missing_ok``."""
        key = read_credential(self._secrets, CredentialKey.COMPANIES_HOUSE)
        token = base64.b64encode(f"{key}:".encode()).decode("ascii")
        headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        http = self._client()
        self._reserve()
        with Metered(self._meter, PROVIDER, operation):  # one ApiCall, whatever the outcome
            try:
                response = http.get(f"{API_BASE}{path}", params=params, headers=headers)
            except Exception:  # noqa: BLE001 - any transport failure: a GET is safe to retry
                raise AdapterError(
                    True, REGISTRY_UNAVAILABLE, "the Companies House request failed"
                ) from None
            document = _answer(response, missing_ok=missing_ok)
            return None if document is None else read(document)


def _answer(response: httpx2.Response, *, missing_ok: bool) -> Document | None:
    status = response.status_code
    if missing_ok and status == NOT_FOUND:
        return None
    if status == 429:
        raise AdapterError(
            True, REGISTRY_RATE_LIMITED, "Companies House answered with HTTP 429"
        ) from None
    if 500 <= status < 600:
        raise AdapterError(
            True, REGISTRY_UNAVAILABLE, f"Companies House answered with HTTP {status}"
        ) from None
    if status in (401, 403):
        raise AdapterError(
            False, REGISTRY_UNAUTHORIZED, f"Companies House refused the API key with HTTP {status}"
        ) from None
    if not 200 <= status < 300:
        raise AdapterError(
            False, REGISTRY_REFUSED, f"Companies House refused the request with HTTP {status}"
        ) from None
    try:
        body: bytes | None = response.content
    except Exception:  # noqa: BLE001 - a body that can't be read is a bad response
        body = None
    if body is None or len(body) > MAX_BODY_BYTES:
        raise AdapterError(
            False, REGISTRY_BAD_RESPONSE, "Companies House answered with an unreadable body"
        ) from None
    try:
        document = json.loads(body)
    except Exception:  # noqa: BLE001 - any body that isn't JSON is a bad response
        document = None
    if not isinstance(document, dict):
        raise AdapterError(
            False,
            REGISTRY_BAD_RESPONSE,
            "Companies House answered with a body that isn't a JSON object",
        ) from None
    return document


def document_hits(document: Document) -> tuple[RegistryCompany, ...]:
    """The valid hits of a search answer, in its order."""
    items = document.get("items")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise AdapterError(
            False,
            REGISTRY_BAD_RESPONSE,
            "Companies House answered a search whose items aren't a list",
        ) from None
    hits: list[RegistryCompany] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        found = _company(
            item.get("company_number"),
            item.get("title"),
            item.get("company_type"),
            item.get("company_status"),
            item.get("address"),
        )
        if found is not None:
            hits.append(found)
    return tuple(hits)


def _items(document: Document, what: str) -> list[Any]:
    items = document.get("items")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise AdapterError(
            False,
            REGISTRY_BAD_RESPONSE,
            f"Companies House answered {what} whose items aren't a list",
        ) from None
    return items


def document_search_hits(document: Document) -> tuple[RegistryCompany, ...]:
    """The valid hits of an advanced search answer, in its order (Story 8.4)."""
    hits: list[RegistryCompany] = []
    for item in _items(document, "an advanced search"):
        if not isinstance(item, Mapping):
            continue
        found = _company(
            item.get("company_number"),
            item.get("company_name"),
            item.get("company_type"),
            item.get("company_status"),
            item.get("registered_office_address"),
            None,
            item.get("sic_codes"),
        )
        if found is not None:
            hits.append(found)
    return tuple(hits)


def document_officers(document: Document) -> tuple[RegistryOfficer, ...]:
    """The valid officers of an officers answer, in its order (Story 8.4)."""
    found: list[RegistryOfficer] = []
    for item in _items(document, "an officers list"):
        if not isinstance(item, Mapping):
            continue
        officer = _officer(item)
        if officer is not None:
            found.append(officer)
    return tuple(found)


def document_company(document: Document) -> RegistryCompany:
    """The company of a profile answer; ``registry_bad_response`` without a valid number and
    name."""
    found = _company(
        document.get("company_number"),
        document.get("company_name"),
        document.get("type"),
        document.get("company_status"),
        document.get("registered_office_address"),
        document.get("accounts"),
        document.get("sic_codes"),
    )
    if found is None:
        raise AdapterError(
            False,
            REGISTRY_BAD_RESPONSE,
            "Companies House answered a profile without a valid company number and name",
        ) from None
    return found

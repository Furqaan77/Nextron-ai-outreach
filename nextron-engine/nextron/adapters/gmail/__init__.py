"""The Gmail adapter for Sending Inboxes (FR-32, AD-9, AD-12; Story 6.2): a Sending Inbox's
Gmail profile, read with ``google-api-python-client`` under the inbox's own ``gmail.modify``
grant.

Import rule: ``nextron.ports``, ``nextron.domain``, ``googleapiclient``, ``google.oauth2`` and
the standard library. With ``nextron.adapters.google_oauth``, it is the only module that names a
Google client library. Nothing here logs.

**``GmailProfiles(*, meter, service=None)``** implements ``GmailPort``. ``service`` builds a
Gmail service object from an access token; without it, ``default_service`` (the live
constructor) is looked up when the first one is built, so a test can refuse it. Google's client
rides on ``httplib2``, which isn't thread-safe, so each thread builds its own service object
(``threading.local``), rebuilt when the access token changes; the token lives only in that
object and in memory.

``default_service(access_token)`` is ``googleapiclient.discovery.build("gmail", "v1",
credentials=google.oauth2.credentials.Credentials(access_token), static_discovery=True,
cache_discovery=False)``: the discovery document bundled with the library, no discovery request
and no cache file.

**``get_profile(access_token)``:**

1. ``access_token`` must be a non-empty ``str`` of printable ASCII; anything else is
   ``gmail_bad_response``, with no request.
2. Inside ``Metered(meter, "gmail", "get_profile")`` (exactly one ``ApiCall`` per request,
   whatever the outcome), the thread's service object for that token (built now if needed;
   a failure to build it is ``gmail_unavailable``, like a transport failure),
3. then ``users().getProfile(userId="me").execute(num_retries=0)``.

=============================================  ================================================
Result                                         Outcome
=============================================  ================================================
a dict with a non-empty ``emailAddress`` str   that address
any other answer                               ``gmail_bad_response``
``HttpError`` 401 or 403                       ``gmail_unauthorized`` (not retryable)
``HttpError`` 429 or 5xx                       ``gmail_unavailable`` (retryable)
``HttpError`` with any other status            ``gmail_bad_response``, naming the status
any other exception from the request           ``gmail_unavailable`` (retryable; a read is safe
                                               to retry)
=============================================  ================================================

``error_for_status(status)`` is that table's ``HttpError`` rows as a pure function. Every error
is an ``AdapterError`` with this module's own text, raised ``from None``: none carries the
token, the address or a body.
"""

import threading
from collections.abc import Callable
from typing import Any, Final, NoReturn

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from nextron.ports.errors import AdapterError
from nextron.ports.gmail import GMAIL_BAD_RESPONSE, GMAIL_UNAUTHORIZED, GMAIL_UNAVAILABLE
from nextron.ports.usage_meter import Metered, UsageMeterPort

PROVIDER: Final = "gmail"  # the usage meter's provider
GET_PROFILE: Final = "get_profile"  # and operation
ADDRESS_FIELD: Final = "emailAddress"
UNAUTHORIZED_STATUSES: Final = frozenset({401, 403})
MAX_TOKEN_LENGTH: Final = 4096


def default_service(access_token: str) -> Any:
    """The live Gmail service object for ``access_token``: the bundled discovery document, no
    discovery request, no cache file."""
    return build(
        "gmail",
        "v1",
        credentials=Credentials(access_token),
        static_discovery=True,
        cache_discovery=False,
    )


def error_for_status(status: int) -> AdapterError:
    """The ``AdapterError`` for a Gmail ``HttpError`` with HTTP ``status``."""
    if status in UNAUTHORIZED_STATUSES:
        return AdapterError(
            False, GMAIL_UNAUTHORIZED, f"Gmail refused the access token with HTTP {status}"
        )
    if status == 429 or 500 <= status < 600:
        return AdapterError(True, GMAIL_UNAVAILABLE, f"Gmail answered with HTTP {status}")
    return AdapterError(False, GMAIL_BAD_RESPONSE, f"Gmail refused the request with HTTP {status}")


def _raise_for_status(status: object) -> NoReturn:
    """Raise ``error_for_status`` of ``status`` (0 when it isn't a number)."""
    try:
        number = int(status)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        number = 0
    found = error_for_status(number)
    raise AdapterError(found.retryable, found.code, found.message) from None


def _checked_token(access_token: object) -> str:
    if (
        not isinstance(access_token, str)
        or not 0 < len(access_token) <= MAX_TOKEN_LENGTH
        or not all("\x21" <= char <= "\x7e" for char in access_token)
    ):
        raise AdapterError(False, GMAIL_BAD_RESPONSE, "the access token is not usable") from None
    return access_token


class GmailProfiles:
    """``GmailPort`` over the Gmail API; see the module docstring."""

    def __init__(
        self, *, meter: UsageMeterPort, service: Callable[[str], Any] | None = None
    ) -> None:
        self._meter = meter
        self._service = service
        self._local = threading.local()

    def _service_for(self, access_token: str) -> Any:
        """This thread's service object for ``access_token``, built when the token changed."""
        local = self._local
        if getattr(local, "token", None) != access_token:
            make = self._service if self._service is not None else default_service
            local.service = make(access_token)  # default_service looked up now
            local.token = access_token
        return local.service

    def get_profile(self, access_token: str) -> str:
        token = _checked_token(access_token)  # step 1
        with Metered(self._meter, PROVIDER, GET_PROFILE):  # steps 2 and 3: one ApiCall
            try:
                service = self._service_for(token)
                answer = service.users().getProfile(userId="me").execute(num_retries=0)
            except HttpError as failed:
                _raise_for_status(failed.status_code)
            except Exception:  # noqa: BLE001 - any transport failure: a read is safe to retry
                raise AdapterError(
                    True, GMAIL_UNAVAILABLE, "the Gmail profile request failed"
                ) from None
            return _address(answer)


def _address(answer: object) -> str:
    """The answer's ``emailAddress``; ``gmail_bad_response`` for anything else."""
    address = answer.get(ADDRESS_FIELD) if isinstance(answer, dict) else None
    if not isinstance(address, str) or not address:
        raise AdapterError(
            False, GMAIL_BAD_RESPONSE, "Gmail answered a profile without an address"
        ) from None
    return address

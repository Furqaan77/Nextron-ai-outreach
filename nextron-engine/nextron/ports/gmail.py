"""The ``GmailPort`` (FR-32, AD-9, AD-12; Story 6.2): a Sending Inbox's Gmail account, read under
its own ``gmail.modify`` grant.

``nextron.adapters.gmail.GmailProfiles`` implements it over the Gmail API (v1
``users.getProfile``, through ``google-api-python-client``); tests use
``tests.fakes.gmail.FakeGmail``. Story 6.2 needs only the profile: the send, sync and
reconciliation calls arrive with Stories 6.8 to 6.12 (``MailPort`` and ``MailboxPort``).

- ``get_profile(access_token) -> str``: the ``emailAddress`` of the account the access token
  belongs to, as Gmail answers it. The identity check compares it with the requested inbox
  (``nextron.domain.sending_inbox.identity_problem``); the address never comes from an ID token.

A failed call raises ``AdapterError`` (``nextron.ports.errors``):

- ``gmail_unauthorized`` (not retryable): Gmail answered 401 or 403, so the access token (or
  its scope) isn't accepted; the caller drops any cached access token;
- ``gmail_unavailable`` (retryable): a network error or timeout, a 429 or a 5xx;
- ``gmail_bad_response``: any other status, or an answer without a non-empty ``emailAddress``
  string, or an access token the adapter refuses before any request.

No message holds the access token, the address or a body.
"""

from typing import Final, Protocol, runtime_checkable

GMAIL_UNAUTHORIZED: Final = "gmail_unauthorized"
GMAIL_UNAVAILABLE: Final = "gmail_unavailable"
GMAIL_BAD_RESPONSE: Final = "gmail_bad_response"
GMAIL_CODES: Final = (GMAIL_UNAUTHORIZED, GMAIL_UNAVAILABLE, GMAIL_BAD_RESPONSE)


@runtime_checkable
class GmailPort(Protocol):
    """Reads a Sending Inbox's Gmail profile; see the module docstring."""

    def get_profile(self, access_token: str) -> str:
        """The ``emailAddress`` of the account ``access_token`` belongs to."""
        ...

"""``FakeGmail``: the scripted fake of ``nextron.ports.gmail.GmailPort``.

``FakeGmail(profiles=None, errors=None)`` answers ``get_profile(access_token)`` from its
scripts, keyed by access token:

- ``profiles`` maps an access token to the ``emailAddress`` Gmail would answer;
- ``errors`` maps an access token to the ``AdapterError`` its call raises (a ``gmail_*``
  code).

An access token in neither raises ``AdapterError`` ``gmail_unauthorized`` (Gmail doesn't know
the token). Both scripts are plain dicts, so a test may change them between calls; ``calls``
records every access token asked about. Unlike the real adapter it meters nothing.
"""

from collections.abc import Mapping

from nextron.ports.errors import AdapterError
from nextron.ports.gmail import GMAIL_UNAUTHORIZED


class FakeGmail:
    """Answers Gmail profile reads from scripts; see the module docstring."""

    def __init__(
        self,
        profiles: Mapping[str, str] | None = None,
        errors: Mapping[str, AdapterError] | None = None,
    ) -> None:
        self.profiles: dict[str, str] = dict(profiles or {})
        self.errors: dict[str, AdapterError] = dict(errors or {})
        self.calls: list[str] = []

    def get_profile(self, access_token: str) -> str:
        self.calls.append(access_token)
        error = self.errors.get(access_token)
        if error is not None:
            raise error
        found = self.profiles.get(access_token)
        if found is None:
            raise AdapterError(False, GMAIL_UNAUTHORIZED, "the fake Gmail doesn't know the token")
        return found

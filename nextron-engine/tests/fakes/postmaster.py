"""``FakePostmaster``: the scripted fake of ``nextron.ports.postmaster.PostmasterPort``.

``FakePostmaster(ppm=None, errors=None)`` answers from its scripts, keyed by domain in lower
case:

- ``ppm`` maps a domain to the day's rate in parts per million, or ``None`` for no data;
- ``errors`` maps a domain to the ``AdapterError`` its query raises (a ``postmaster_*`` code,
  or a token error such as ``google_not_granted``).

A domain not scripted reads as no data (``PostmasterReading(None)``). The scripts are plain
dicts (``rates`` and ``errors``), so a test may change them between queries. ``calls`` records
every query as a ``(domain, day)`` pair. Unlike the real adapter it meters nothing.
"""

from collections.abc import Mapping
from datetime import date

from nextron.ports.errors import AdapterError
from nextron.ports.postmaster import PostmasterReading


class FakePostmaster:
    """Answers spam-rate queries from scripts; see the module docstring."""

    def __init__(
        self,
        ppm: Mapping[str, int | None] | None = None,
        errors: Mapping[str, AdapterError] | None = None,
    ) -> None:
        self.rates: dict[str, int | None] = {
            domain.lower(): value for domain, value in (ppm or {}).items()
        }
        self.errors: dict[str, AdapterError] = {
            domain.lower(): error for domain, error in (errors or {}).items()
        }
        self.calls: list[tuple[str, date]] = []

    def spam_rate(self, domain: str, day: date) -> PostmasterReading:
        self.calls.append((domain, day))
        found = domain.lower()
        error = self.errors.get(found)
        if error is not None:
            raise error
        return PostmasterReading(self.rates.get(found))

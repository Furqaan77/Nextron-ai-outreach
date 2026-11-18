"""The ``DnsPort`` (FR-64, NFR-4, NFR-20, AD-9): TXT and MX lookups.

``nextron.adapters.dns.DnspythonDns`` implements it over dnspython; tests use
``tests.fakes.dns.FakeDns``. Story 6.5 reads the TXT records of a Sending Domain's SPF, DKIM and
DMARC names through it; Story 2.14 (MX checks) and Story 6.6 (DNSBL) reuse it.

- **``txt(name) -> tuple[str, ...]``**: one string per TXT record, its character-strings joined
  with nothing (RFC 7208 §3.3), decoded as UTF-8 with replacement, the tuple sorted.
- **``mx(name) -> tuple[MxRecord, ...]``**: one ``MxRecord(preference, exchange)`` per MX
  record, sorted by ``(preference, exchange)``. ``exchange`` is lower-case with no final dot; a
  null MX (RFC 7505, ``.``) has ``exchange == ""``.

A name that doesn't exist (NXDOMAIN) and a name with no record of the type (NODATA) both answer
``()``. A name that isn't a ``str`` raises ``TypeError``.

**``is_dns_name(name)``** is the name rule both the adapter and the fake apply before any
query: a non-empty ASCII DNS name, made of labels of 1 to 63 letters, digits, ``-`` or ``_``
separated by single dots, with an optional final dot, at most 253 characters without it. It
raises ``TypeError`` for a non-``str``.

**Failures.** Every other failure raises ``AdapterError`` with one of ``DNS_CODES``:

- ``dns_timeout`` (retryable): the query's lifetime ran out;
- ``dns_unavailable`` (retryable): no nameserver answered usably (SERVFAIL, REFUSED or a
  network error);
- ``dns_invalid_name``: the name isn't a non-empty ASCII DNS name; checked before any query;
- ``dns_failed``: any other DNS error.
"""

import re
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

DNS_TIMEOUT: Final = "dns_timeout"
DNS_UNAVAILABLE: Final = "dns_unavailable"
DNS_INVALID_NAME: Final = "dns_invalid_name"
DNS_FAILED: Final = "dns_failed"
DNS_CODES: Final = (DNS_TIMEOUT, DNS_UNAVAILABLE, DNS_INVALID_NAME, DNS_FAILED)
RETRYABLE_CODES: Final = frozenset({DNS_TIMEOUT, DNS_UNAVAILABLE})
MAX_NAME_LENGTH: Final = 253  # without the final dot
NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,63}(?:\.[A-Za-z0-9_-]{1,63})*\.?")


def is_dns_name(name: object) -> bool:
    """Whether ``name`` is a non-empty ASCII DNS name (see the module docstring); raises
    ``TypeError`` for a non-``str``."""
    if not isinstance(name, str):
        raise TypeError("a DNS name must be a str")
    bare = name.removesuffix(".")
    return (
        0 < len(bare) <= MAX_NAME_LENGTH
        and name.isascii()
        and NAME_PATTERN.fullmatch(name) is not None
    )


@dataclass(frozen=True, slots=True, order=True)
class MxRecord:
    """One MX record: its ``preference`` and its lower-case ``exchange`` with no final dot
    (``""`` for a null MX)."""

    preference: int
    exchange: str

    def __post_init__(self) -> None:
        if isinstance(self.preference, bool) or not isinstance(self.preference, int):
            raise TypeError("an MX record's preference must be an int")
        if not isinstance(self.exchange, str):
            raise TypeError("an MX record's exchange must be a str")


@runtime_checkable
class DnsPort(Protocol):
    """Looks up TXT and MX records; see the module docstring."""

    def txt(self, name: str) -> tuple[str, ...]:
        """The TXT records at ``name``, each joined and decoded, sorted; ``()`` for none."""
        ...

    def mx(self, name: str) -> tuple[MxRecord, ...]:
        """The MX records at ``name``, sorted by ``(preference, exchange)``; ``()`` for
        none."""
        ...

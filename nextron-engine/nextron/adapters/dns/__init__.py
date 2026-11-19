"""The DNS adapter (FR-64, NFR-4, NFR-20, AD-9): ``DnsPort`` over dnspython.

Import rule: ``nextron.ports``, ``dns`` (dnspython) and the standard library. It is the only
module that imports ``dns``. Nothing here logs.

**``default_resolver()``** returns ``dns.resolver.Resolver()``, built from the system's
resolver configuration: the live constructor, which ``tests/live_guard.py`` refuses in tests.

**``DnspythonDns(*, meter, resolver=None, lifetime=5.0)``** implements ``DnsPort``. Building it
builds nothing and reads no configuration. Its first query builds the resolver, under a lock,
through ``default_resolver()`` (looked up then, so a test can refuse it), unless one was
injected, and later queries reuse it. After a retryable failure (``dns_timeout`` or
``dns_unavailable``) a built resolver is forgotten, so the next query builds it again from the
system configuration then (the PC may have changed network or VPN since); an injected one is
kept.

**Each query** (``txt``, ``mx`` or ``a``, Story 6.6's DNSBL lookups):

1. the name is checked with ``nextron.ports.dns.is_dns_name``: a non-``str`` raises
   ``TypeError``; a name that isn't a non-empty ASCII DNS name (labels of 1 to 63 letters,
   digits, ``-`` or ``_``, separated by single dots, with an optional final dot, at most 253
   characters without it) is ``dns_invalid_name``;
2. it is made absolute (``dns.name.from_text``); a name dnspython refuses is
   ``dns_invalid_name`` too;
3. inside ``Metered(meter, "dns", "txt" | "mx" | "a")``, ``resolver.resolve(name, type,
   lifetime=lifetime, search=False)`` is called exactly once.

So each query records one ``ApiCall``, and a refused name records none.

**Answers.** NXDOMAIN and NODATA (``dns.resolver.NXDOMAIN``, ``dns.resolver.NoAnswer``) answer
``()``. A TXT record's character-strings are joined with nothing and decoded as UTF-8 with
replacement; an MX record's exchange is lower-cased without its final dot, and the root (a null
MX, RFC 7505) is ``""``; an A record is its dotted IPv4 address. Every answer is sorted.

**Errors.** A timeout (``dns.exception.Timeout``, ``LifetimeTimeout`` included) is the
retryable ``dns_timeout``; ``dns.resolver.NoNameservers`` (SERVFAIL, REFUSED or a network error
at every nameserver) and an ``OSError`` are the retryable ``dns_unavailable``; any other
``dns.exception.DNSException`` is ``dns_failed``. A resolver that can't be built from the system
configuration (a ``DNSException`` such as ``NoResolverConfiguration``, or an ``OSError``: the
network isn't up yet, say, at logon) is the retryable ``dns_unavailable``, raised before
``Metered``, so it records nothing and nothing is kept. Every error is an ``AdapterError`` with
this module's own message, raised ``from None``: none carries the name asked for.
"""

import threading
from typing import Final

import dns.exception
import dns.name
import dns.rdatatype
import dns.resolver

from nextron.ports.dns import (
    DNS_FAILED,
    DNS_INVALID_NAME,
    DNS_TIMEOUT,
    DNS_UNAVAILABLE,
    MxRecord,
    is_dns_name,
)
from nextron.ports.errors import AdapterError
from nextron.ports.usage_meter import Metered, UsageMeterPort

PROVIDER: Final = "dns"  # the usage meter's provider and operations
TXT: Final = "txt"
MX: Final = "mx"
A: Final = "a"
DEFAULT_LIFETIME_S: Final = 5.0


def default_resolver() -> dns.resolver.Resolver:
    """The live resolver, built from the system's resolver configuration."""
    return dns.resolver.Resolver()


def _checked_name(name: str) -> dns.name.Name:
    """Steps 1 and 2: ``name`` as an absolute dnspython name; ``TypeError`` (from
    ``is_dns_name``) or ``dns_invalid_name`` otherwise."""
    if not is_dns_name(name):
        raise AdapterError(False, DNS_INVALID_NAME, "the name is not a DNS name")
    try:
        return dns.name.from_text(name)
    except dns.exception.DNSException:
        raise AdapterError(False, DNS_INVALID_NAME, "the name is not a DNS name") from None


def _exchange(name: dns.name.Name) -> str:
    """An MX exchange: lower-case without the final dot; the root (a null MX) is ``""``."""
    if name == dns.name.root:
        return ""
    return name.to_text(omit_final_dot=True).lower()


class DnspythonDns:
    """``DnsPort`` over dnspython; see the module docstring."""

    def __init__(
        self,
        *,
        meter: UsageMeterPort,
        resolver: dns.resolver.Resolver | None = None,
        lifetime: float = DEFAULT_LIFETIME_S,
    ) -> None:
        self._meter = meter
        self._resolver = resolver
        self._injected = resolver is not None
        self._lifetime = lifetime
        self._lock = threading.Lock()

    def _live_resolver(self) -> dns.resolver.Resolver:
        with self._lock:
            if self._resolver is None:
                try:
                    self._resolver = default_resolver()  # looked up now, so tests can refuse it
                except (dns.exception.DNSException, OSError):
                    raise AdapterError(
                        True, DNS_UNAVAILABLE, "the system's DNS configuration could not be read"
                    ) from None
            return self._resolver

    def _forget(self, resolver: dns.resolver.Resolver) -> None:
        """After a retryable failure: drop ``resolver`` unless it was injected (or another
        query has replaced it already), so the next query builds a fresh one."""
        with self._lock:
            if not self._injected and self._resolver is resolver:
                self._resolver = None

    def _resolve(
        self, name: str, rdtype: dns.rdatatype.RdataType, operation: str
    ) -> dns.resolver.Answer | None:
        """Steps 1 to 3: the answer, or ``None`` for NXDOMAIN and NODATA."""
        absolute = _checked_name(name)
        resolver = self._live_resolver()
        with Metered(self._meter, PROVIDER, operation):
            try:
                return resolver.resolve(absolute, rdtype, lifetime=self._lifetime, search=False)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                return None
            except dns.exception.Timeout:
                self._forget(resolver)
                raise AdapterError(True, DNS_TIMEOUT, "the DNS query timed out") from None
            except dns.resolver.NoNameservers:
                self._forget(resolver)
                raise AdapterError(
                    True, DNS_UNAVAILABLE, "no DNS server answered the query"
                ) from None
            except dns.exception.DNSException:
                raise AdapterError(False, DNS_FAILED, "the DNS query failed") from None
            except OSError:
                self._forget(resolver)
                raise AdapterError(
                    True, DNS_UNAVAILABLE, "the DNS query could not reach a server"
                ) from None

    def txt(self, name: str) -> tuple[str, ...]:
        answer = self._resolve(name, dns.rdatatype.TXT, TXT)
        if answer is None:
            return ()
        return tuple(
            sorted(
                b"".join(record.strings).decode("utf-8", errors="replace") for record in answer
            )
        )

    def mx(self, name: str) -> tuple[MxRecord, ...]:
        answer = self._resolve(name, dns.rdatatype.MX, MX)
        if answer is None:
            return ()
        return tuple(sorted(MxRecord(int(rr.preference), _exchange(rr.exchange)) for rr in answer))

    def a(self, name: str) -> tuple[str, ...]:
        answer = self._resolve(name, dns.rdatatype.A, A)
        if answer is None:
            return ()
        return tuple(sorted(str(record.address) for record in answer))

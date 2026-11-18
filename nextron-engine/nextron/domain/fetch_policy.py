"""What the engine may fetch (FR-49, AD-9, NFR-6): the denylist and the egress rules. Standard
library only.

**The denylist.** LinkedIn, Instagram, Google Maps and the job sites are never fetched: such a
URL may be stored as a source, never requested. ``is_denied(url)`` is true when any of these
holds:

1. ``identity.url_host(url)`` refuses the URL (``IdentityError``): fail closed, so a URL whose
   host can't be read is never fetched;
2. the host is, or is a subdomain of, one of ``DENIED_DOMAINS`` (``linkedin.com``, ``lnkd.in``,
   ``instagram.com``, ``instagr.am``, ``ziprecruiter.com``, ``maps.app.goo.gl``,
   ``reed.co.uk``, ``totaljobs.com``, ``cv-library.co.uk``, ``g.page``, and, from Story 10.1,
   the platforms' own static CDNs ``licdn.com`` and ``cdninstagram.com``, which serve LinkedIn
   badges and Instagram embeds), so ``www.linkedin.com`` matches and ``notlinkedin.com``
   doesn't;
3. the host's registrable domain (``identity.registrable_domain(host)``; a refusal there means
   no match) has a first label in ``DENIED_BRANDS`` (``indeed``, ``glassdoor``), so
   ``indeed.com``, ``uk.indeed.com`` and ``glassdoor.co.uk`` match and ``indeedjobs.com``
   doesn't;
4. Google Maps: a ``maps`` label anywhere left of a registrable domain whose first label is
   ``google`` (``maps.google.com``, ``www.maps.google.com``, ``maps.google.co.uk``); or the
   registrable domain's first label is ``google``, or it is ``goo.gl``, and the URL's
   normalised path is ``/maps`` or starts with ``/maps/`` (``www.google.com/maps/place/x``).
   The path is percent-decoded and lowercased, each segment loses its ``;`` parameters,
   repeated slashes collapse to one and dot segments are resolved (``posixpath.normpath``,
   keeping the leading ``/``), so ``google.com//maps/x``, ``google.com/maps;x`` and
   ``google.com/x/../maps`` are denied too.

A ``str`` is required (``TypeError`` otherwise, as in ``nextron.domain.identity``). Story 2.3's
CSV import refuses a denied website. Story 2.5's fetching adapters check every first URL and
every redirect against this list (``tests/contracts/test_fetch_policy_contracts.py``).

**The egress rules (AD-9).** A fetching adapter runs every hop through them:

- ``check_url(url) -> FetchTarget(url, scheme, host, port, path)`` raises ``FetchRefused(reason)``
  (code ``fetch_refused``; its message is the reason, never the URL). The reasons, checked in
  this order:

  - ``bad_url``: not parsable (``urllib.parse.urlsplit`` raises), longer than 2,048
    characters (``MAX_URL_LENGTH``), empty, holding whitespace or a ``Cc`` or ``Cf``
    character, without a host, with a port that isn't a number from 0 to 65535, or with a host
    ``identity.url_host`` refuses;
  - ``scheme``: anything but ``http`` or ``https`` (in any case; no scheme at all included);
  - ``userinfo``: credentials (``user@`` or ``user:password@``) in the URL;
  - ``port``: an explicit port other than 80 or 443;
  - ``ip_host``: the host is an IP literal: an address ``ipaddress`` reads (IPv6 in brackets
    included), or a name whose last label is a number in decimal or ``0x`` hex
    (``127.1``, ``0x7f.1``), which browsers and resolvers read as IPv4;
  - ``denylisted``: ``is_denied(url)``.

  ``FetchTarget.url`` is the URL with its scheme lowercased, its host as ``url_host`` gives it
  (ASCII, lowercased, IDNA-encoded), an explicit port kept unless it is the scheme's default (so
  ``https://x:443/team`` and ``https://x/team`` are one URL), an empty path written ``/``, the
  query kept and the fragment dropped. ``port`` is the explicit port, else 80 for ``http`` and
  443 for ``https``; ``path`` is the path alone (``/`` when empty). ``url_refusal(url)`` is the
  reason ``check_url`` would raise, or ``None``, for callers that report the reason without
  catching the error.

- ``address_refusal(address, own_addresses) -> str | None`` judges one resolved address (an
  ``IPv4Address`` or ``IPv6Address``; ``TypeError`` otherwise). The reasons, in order:
  ``unspecified``, ``loopback``, ``link_local``, ``multicast``; ``cgnat`` (100.64.0.0/10),
  ``private`` (RFC 1918: 10/8, 172.16/12, 192.168/16) and ``ula`` (fc00::/7);
  ``ipv4_embedded`` (IPv4-mapped ``::ffff:0:0/96``, the deprecated IPv4-compatible ``::/96``,
  6to4 ``2002::/16``, Teredo ``2001::/32``, and NAT64 ``64:ff9b::/96`` and
  ``64:ff9b:1::/48``); ``not_global``: any other address whose ``is_global`` is false; and
  ``own_address``: an address in ``own_addresses`` (compared without an IPv6 scope). ``None``
  means the address may be fetched.

- ``OFF_SITE`` (``off_site``) is the reason an adapter gives when a hop leaves the registrable
  domain its caller pinned.

**Limits.** ``MAX_REDIRECTS`` (5), ``MAX_PAGE_BYTES`` (5 MiB), ``MAX_ROBOTS_BYTES`` (512 KiB)
and ``PAGE_MEDIA_TYPES`` (``text/html``, ``application/xhtml+xml``, ``application/json``).
Nothing here takes a setting or a parameter that loosens a rule.
"""

import ipaddress
import posixpath
import re
import unicodedata
import urllib.parse
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.identity import (
    SCHEME_PATTERN,
    IdentityError,
    registrable_domain,
    url_host,
)

DENIED_DOMAINS: Final = (
    "linkedin.com",
    "lnkd.in",
    "instagram.com",
    "instagr.am",
    "ziprecruiter.com",
    "maps.app.goo.gl",
    "reed.co.uk",
    "totaljobs.com",
    "cv-library.co.uk",
    "g.page",
    "licdn.com",
    "cdninstagram.com",
)
DENIED_BRANDS: Final = frozenset({"indeed", "glassdoor"})
MAPS_LABEL: Final = "maps"
GOOGLE_LABEL: Final = "google"
GOOGLE_SHORT_DOMAIN: Final = "goo.gl"
MAPS_PATH: Final = "/maps"
REPEATED_SLASHES: Final = re.compile("/{2,}")

# The egress rules ---------------------------------------------------------------------------

MAX_URL_LENGTH: Final = 2048
MAX_REDIRECTS: Final = 5
MAX_PAGE_BYTES: Final = 5 * 1024 * 1024
MAX_ROBOTS_BYTES: Final = 512 * 1024
PAGE_MEDIA_TYPES: Final = frozenset({"text/html", "application/xhtml+xml", "application/json"})
WEB_SCHEMES: Final = frozenset({"http", "https"})
DEFAULT_PORTS: Final = {"http": 80, "https": 443}
ALLOWED_PORTS: Final = frozenset({80, 443})
NUMERIC_LABEL: Final = re.compile(r"[0-9]+|0[xX][0-9a-fA-F]*")  # matched with fullmatch

# check_url's reasons, in the order it checks them
BAD_URL: Final = "bad_url"
SCHEME: Final = "scheme"
USERINFO: Final = "userinfo"
PORT: Final = "port"
IP_HOST: Final = "ip_host"
DENYLISTED: Final = "denylisted"
URL_REASONS: Final = (BAD_URL, SCHEME, USERINFO, PORT, IP_HOST, DENYLISTED)

# address_refusal's reasons, in the order it checks them
UNSPECIFIED: Final = "unspecified"
LOOPBACK: Final = "loopback"
LINK_LOCAL: Final = "link_local"
MULTICAST: Final = "multicast"
CGNAT: Final = "cgnat"
PRIVATE: Final = "private"
ULA: Final = "ula"
IPV4_EMBEDDED: Final = "ipv4_embedded"
NOT_GLOBAL: Final = "not_global"
OWN_ADDRESS: Final = "own_address"
ADDRESS_REASONS: Final = (
    UNSPECIFIED,
    LOOPBACK,
    LINK_LOCAL,
    MULTICAST,
    CGNAT,
    PRIVATE,
    ULA,
    IPV4_EMBEDDED,
    NOT_GLOBAL,
    OWN_ADDRESS,
)
OFF_SITE: Final = "off_site"  # an adapter's reason: a hop left the pinned registrable domain

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

CGNAT_NETWORK: Final = ipaddress.ip_network("100.64.0.0/10")
PRIVATE_NETWORKS: Final = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
ULA_NETWORK: Final = ipaddress.ip_network("fc00::/7")
IPV4_COMPATIBLE_NETWORK: Final = ipaddress.ip_network("::/96")
NAT64_NETWORKS: Final = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


class FetchRefused(DomainError):
    """A URL the egress rules refuse; the message is the reason, never the URL."""

    code = "fetch_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FetchTarget:
    """A URL the egress rules accept, split for the request; see the module docstring."""

    url: str
    scheme: str
    host: str
    port: int
    path: str


# The denylist ---------------------------------------------------------------------------------


def _under(host: str, domain: str) -> bool:
    """Whether ``host`` is ``domain`` or one of its subdomains."""
    return host == domain or host.endswith("." + domain)


def _registrable(host: str) -> str | None:
    try:
        return registrable_domain(host)
    except IdentityError:
        return None


def _path(url: str) -> str:
    """The URL's path, normalised as the module docstring says; ``url`` is one ``url_host``
    read."""
    text = url.strip()
    if SCHEME_PATTERN.match(text) is None:
        text = "http://" + text
    try:
        path = urllib.parse.urlsplit(text).path
    except ValueError:  # url_host read it, so this can't happen; fail closed all the same
        return MAPS_PATH
    decoded = urllib.parse.unquote(path).lower()
    segments = "/".join(segment.split(";", 1)[0] for segment in decoded.split("/"))
    return posixpath.normpath(REPEATED_SLASHES.sub("/", "/" + segments))


def _is_maps_path(path: str) -> bool:
    return path == MAPS_PATH or path.startswith(MAPS_PATH + "/")


def _left_labels(host: str, registrable: str) -> list[str]:
    """The labels of ``host`` left of its registrable domain."""
    labels = host.split(".")
    return labels[: len(labels) - len(registrable.split("."))]


def is_denied(url: str) -> bool:
    """Whether the engine must never fetch ``url``; see the module docstring."""
    try:
        host = url_host(url)
    except IdentityError:
        return True
    if any(_under(host, domain) for domain in DENIED_DOMAINS):
        return True
    registrable = _registrable(host)
    if registrable is None:
        return False
    brand = registrable.split(".", 1)[0]
    if brand in DENIED_BRANDS:
        return True
    is_google = brand == GOOGLE_LABEL
    if is_google and MAPS_LABEL in _left_labels(host, registrable):
        return True
    return (is_google or registrable == GOOGLE_SHORT_DOMAIN) and _is_maps_path(_path(url))


# The egress rules -----------------------------------------------------------------------------


def _is_ip_literal(hostname: str) -> bool:
    """Whether ``hostname`` (as ``urlsplit`` gives it) is an IP address in any form a browser or
    resolver reads as one."""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return True
    if "%" in hostname or ":" in hostname:  # a scoped or bracketed address ipaddress refused
        return True
    labels = hostname.rstrip(".").split(".")
    return NUMERIC_LABEL.fullmatch(labels[-1]) is not None


def _has_space_control_or_format(text: str) -> bool:
    return any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def _check(url: str) -> FetchTarget | str:
    """The target ``url`` names, or the first reason it is refused."""
    if not isinstance(url, str):
        raise TypeError(f"check_url takes a str, not {type(url).__name__}")
    if not url or len(url) > MAX_URL_LENGTH or _has_space_control_or_format(url):
        return BAD_URL
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return BAD_URL
    scheme = parts.scheme.lower()
    if scheme not in WEB_SCHEMES:
        return SCHEME
    if not hostname:
        return BAD_URL
    if "@" in parts.netloc:
        return USERINFO
    try:
        explicit = parts.port
    except ValueError:
        return BAD_URL
    if explicit is not None and explicit not in ALLOWED_PORTS:
        return PORT
    if _is_ip_literal(hostname):
        return IP_HOST
    try:
        host = url_host(url)
    except IdentityError:
        return BAD_URL
    if is_denied(url):
        return DENYLISTED
    port = DEFAULT_PORTS[scheme] if explicit is None else explicit
    netloc = host if port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    path = parts.path or "/"
    canonical = urllib.parse.urlunsplit((scheme, netloc, path, parts.query, ""))
    return FetchTarget(url=canonical, scheme=scheme, host=host, port=port, path=path)


def url_refusal(url: str) -> str | None:
    """The reason ``check_url(url)`` would refuse ``url``, or ``None``."""
    checked = _check(url)
    return checked if isinstance(checked, str) else None


def check_url(url: str) -> FetchTarget:
    """``url`` split for the request; raises ``FetchRefused`` with the first reason it is
    refused (see the module docstring)."""
    checked = _check(url)
    if isinstance(checked, str):
        raise FetchRefused(checked)
    return checked


def _plain(address: IpAddress) -> IpAddress:
    """``address`` without an IPv6 scope, so it compares by value alone."""
    if isinstance(address, ipaddress.IPv6Address):
        return ipaddress.IPv6Address(address.packed)
    return address


def _embeds_ipv4(address: ipaddress.IPv6Address) -> bool:
    return (
        address.ipv4_mapped is not None
        or address in IPV4_COMPATIBLE_NETWORK
        or address.sixtofour is not None
        or address.teredo is not None
        or any(address in network for network in NAT64_NETWORKS)
    )


def address_refusal(address: IpAddress, own_addresses: Collection[IpAddress]) -> str | None:
    """Why ``address`` may not be fetched, or ``None``; see the module docstring."""
    if not isinstance(address, ipaddress.IPv4Address | ipaddress.IPv6Address):
        raise TypeError(
            f"address_refusal takes an IPv4Address or IPv6Address, not {type(address).__name__}"
        )
    plain = _plain(address)
    if plain.is_unspecified:
        return UNSPECIFIED
    if plain.is_loopback:
        return LOOPBACK
    if plain.is_link_local:
        return LINK_LOCAL
    if plain.is_multicast:
        return MULTICAST
    if isinstance(plain, ipaddress.IPv4Address):
        if plain in CGNAT_NETWORK:
            return CGNAT
        if any(plain in network for network in PRIVATE_NETWORKS):
            return PRIVATE
    else:
        if plain in ULA_NETWORK:
            return ULA
        if _embeds_ipv4(plain):
            return IPV4_EMBEDDED
    if not plain.is_global:
        return NOT_GLOBAL
    if plain in {_plain(own) for own in own_addresses}:
        return OWN_ADDRESS
    return None

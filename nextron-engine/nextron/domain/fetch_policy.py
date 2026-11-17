"""The FR-49 denylist: URLs the engine never fetches. Standard library only.

LinkedIn, Instagram, Google Maps and the job sites are never fetched: such a URL may be stored as
a source, never requested. ``is_denied(url)`` is true when any of these holds:

1. ``identity.url_host(url)`` refuses the URL (``IdentityError``): fail closed, so a URL whose
   host can't be read is never fetched;
2. the host is, or is a subdomain of, one of ``DENIED_DOMAINS`` (``linkedin.com``, ``lnkd.in``,
   ``instagram.com``, ``instagr.am``, ``ziprecruiter.com``, ``maps.app.goo.gl``), so
   ``www.linkedin.com`` matches and ``notlinkedin.com`` doesn't;
3. the host's registrable domain (``identity.registrable_domain(host)``; a refusal there means
   no match) has a first label in ``DENIED_BRANDS`` (``indeed``, ``glassdoor``), so
   ``indeed.com``, ``uk.indeed.com`` and ``glassdoor.co.uk`` match and ``indeedjobs.com``
   doesn't;
4. Google Maps: the host's first label is ``maps`` under a registrable domain whose first label
   is ``google`` (``maps.google.com``, ``maps.google.co.uk``); or the registrable domain's first
   label is ``google``, or it is ``goo.gl``, and the URL's normalised path is ``/maps`` or
   starts with ``/maps/`` (``www.google.com/maps/place/x``). The path is percent-decoded and
   lowercased, each segment loses its ``;`` parameters, repeated slashes collapse to one and dot
   segments are resolved (``posixpath.normpath``, keeping the leading ``/``), so
   ``google.com//maps/x``, ``google.com/maps;x`` and ``google.com/x/../maps`` are denied too.

A ``str`` is required (``TypeError`` otherwise, as in ``nextron.domain.identity``). Story 2.3's
CSV import refuses a denied website. Story 2.5 adds the fetching adapters, which check every
first URL and every redirect against this list, and their contract test.
"""

import posixpath
import re
import urllib.parse
from typing import Final

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
)
DENIED_BRANDS: Final = frozenset({"indeed", "glassdoor"})
MAPS_LABEL: Final = "maps"
GOOGLE_LABEL: Final = "google"
GOOGLE_SHORT_DOMAIN: Final = "goo.gl"
MAPS_PATH: Final = "/maps"
REPEATED_SLASHES: Final = re.compile("/{2,}")


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
    if is_google and host.split(".", 1)[0] == MAPS_LABEL:
        return True
    return (is_google or registrable == GOOGLE_SHORT_DOMAIN) and _is_maps_path(_path(url))

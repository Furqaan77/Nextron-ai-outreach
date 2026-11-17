"""The website crawler (FR-10, FR-49, FR-57, AD-9, AD-18, NFR-6, NFR-34): ``CrawlerPort`` over
``httpx2``, ``selectolax`` and ``protego``, inside the egress filter.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2``, ``selectolax``, ``protego`` and
the standard library. Nothing here logs.

**``HttpCrawler(*, meter, http=None, resolve=None, own_addresses=None, monotonic=time.monotonic,
sleep=time.sleep)``** implements ``CrawlerPort``. Its live parts are built on first use, looked
up when they are called (so a test can refuse them):

- ``default_http_client(transport=None)``: an ``httpx2.Client`` that never follows redirects,
  ignores proxy and certificate settings from the environment (``trust_env=False``), keeps no
  cookie (its jar's policy refuses every cookie), keeps no connection alive
  (``max_keepalive_connections=0`` and ``Connection: close``) and sends ``USER_AGENT``; built
  once, under a lock. ``transport`` lets a test watch what the real client sends.
- ``default_resolver()``: a resolver over ``socket.getaddrinfo`` (TCP), returning each
  distinct address as text; built once, under a lock.
- ``default_own_addresses()``: this PC's own addresses (``getaddrinfo`` of its host name), read
  at every fetch when ``own_addresses`` isn't given. When they can't be read the fetch fails
  (``fetch_failed``) before its first hop, so the own-address check is never silently off.

**Each hop** of ``fetch_robots`` and ``fetch_page``:

1. ``fetch_policy.check_url``, then the ``site`` check (the host's registrable domain must be
   ``site`` when it is given): a refusal is ``fetch_refused`` naming its reason, before any
   lookup. The first URL is checked before any live part is built;
2. the host is resolved here, never by the HTTP client: a lookup that fails or returns nothing
   is ``fetch_dns_failed``, and when any returned address fails
   ``fetch_policy.address_refusal`` the hop is ``fetch_refused`` naming the reason;
3. pacing: requests to one registrable domain (the host itself when it has none) start at
   least ``max(min_interval_s, 1)`` seconds apart, across hops, calls and threads: each request
   reserves its slot under a lock, then sleeps until it;
4. inside ``Metered(meter, "web", "robots" | "page")`` (one ``ApiCall`` per request, whatever
   its outcome), exactly one GET goes to the first address: the URL carries the IP, the
   ``Host`` header the name (with a port other than the scheme's), ``extensions={"sni_hostname":
   host}`` makes TLS check the name, ``follow_redirects=False``, and ``timeout`` the time the
   fetch has left (connect at most 10 s);
5. a 301, 302, 303, 307 or 308 with a ``Location`` is resolved against the hop's own URL (the
   name, not the IP) and starts the next hop. The sixth redirect raises
   ``fetch_too_many_redirects`` before any request, so a fetch makes at most six.

**One deadline per fetch.** ``REQUEST_DEADLINE_S`` (30 s, on ``monotonic``) starts when the
fetch's first request is about to go and covers every later hop, its lookup and paced wait
included, and every body read. It is checked before each hop, again before each request, and
after each chunk of a body; past it the fetch raises the retryable ``fetch_timeout``. A status
outside 100 to 599 is ``fetch_failed``.

**The answer.** Any other status ends the fetch. A non-2xx answer's body is never read. A 2xx
answer must have a media type in ``fetch_policy.PAGE_MEDIA_TYPES`` (robots.txt: any ``text/*``),
else ``fetch_unsupported_type``; its body may not pass ``MAX_PAGE_BYTES`` (robots.txt:
``MAX_ROBOTS_BYTES``), checked against ``Content-Length`` before reading and against the
decoded bytes while reading, else ``fetch_too_large``. A page is decoded and parsed by
``nextron.adapters.crawler.parsing`` (``text/html`` and ``application/xhtml+xml``: the visible
text and links; ``application/json``: ``visible_text`` of the body, no links, no HTML).
robots.txt is decoded as ``utf-8-sig`` (a byte-order mark breaks protego) with replacement and
parsed by protego; ``allows(url)`` is protego's ``can_fetch(url, ROBOTS_TOKEN)`` (``False``
when protego raises) and ``crawl_delay_s`` its ``crawl_delay(ROBOTS_TOKEN)``.

**``fetch_resource(url, *, site, accepts, accept_header, operation, deadline, min_interval_s)
-> Resource(final_url, status, media_type, charset, headers, body)``** (Story 2.6, for the
renderer) runs the same hops as a page fetch, every check, the pinned address, the redirects and
``MAX_PAGE_BYTES`` included, with one ``ApiCall`` per request metered as ``("web",
operation)``. ``min_interval_s=None`` means unpaced (no slot is taken); ``deadline`` (on
``monotonic``, or ``None``) caps the fetch's own 30 s deadline and is checked before the first
hop too; a 2xx media type must satisfy ``accepts`` (a predicate), else
``fetch_unsupported_type``; ``accept_header`` is sent as ``Accept``. The answer's body is
returned undecoded (a non-2xx answer's is empty, as for a page), and ``headers`` holds only
``access-control-allow-origin`` and ``access-control-allow-credentials`` (``CORS_HEADERS``) of
the final answer. ``check_target(url, site)`` is step 1 of a hop on its own: the ``FetchTarget``
when the egress rules and the ``site`` pin accept ``url``, else ``fetch_refused``.

**Errors.** A timeout (``httpx2.TimeoutException`` or the deadline) is retryable
``fetch_timeout``; ``httpx2.ConnectError`` (refused, unreachable, a TLS failure) is
``fetch_connect_failed``; anything else the request raises is ``fetch_failed``. Every error is
an ``AdapterError`` with this module's own message, raised ``from None``: none carries a URL,
an address, a header or any page text.
"""

import functools
import ipaddress
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from types import MappingProxyType
from typing import Final
from urllib.request import Request

import httpx2
from protego import Protego

from nextron.adapters.crawler.parsing import content_type, decode, parse_html
from nextron.domain.crawl import ROBOTS_TOKEN, USER_AGENT, request_interval, visible_text
from nextron.domain.fetch_policy import (
    BAD_URL,
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    MAX_ROBOTS_BYTES,
    OFF_SITE,
    PAGE_MEDIA_TYPES,
    FetchTarget,
    IpAddress,
    address_refusal,
    check_url,
    url_refusal,
)
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.ports.crawler import (
    FETCH_CONNECT_FAILED,
    FETCH_DNS_FAILED,
    FETCH_FAILED,
    FETCH_REFUSED,
    FETCH_TIMEOUT,
    FETCH_TOO_LARGE,
    FETCH_TOO_MANY_REDIRECTS,
    FETCH_UNSUPPORTED_TYPE,
    FetchedPage,
    Robots,
)
from nextron.ports.errors import AdapterError
from nextron.ports.usage_meter import Metered, UsageMeterPort

PROVIDER: Final = "web"  # the usage meter's provider and operations
ROBOTS: Final = "robots"
PAGE: Final = "page"
REQUEST_DEADLINE_S: Final = 30.0
CONNECT_TIMEOUT_S: Final = 10.0
MIN_TIMEOUT_S: Final = 0.001  # httpx2 takes a positive timeout
MIN_STATUS: Final = 100
MAX_STATUS: Final = 599
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
JSON_MEDIA_TYPE: Final = "application/json"
TEXT_MEDIA_PREFIX: Final = "text/"
PAGE_ACCEPT: Final = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1"
ROBOTS_ACCEPT: Final = "text/plain,*/*;q=0.1"
DEFAULT_PORTS: Final = {"http": 80, "https": 443}
PACE_PRUNE_SIZE: Final = 1024  # past this many sites, slots long gone are forgotten
PACE_MEMORY_S: Final = 60.0
# The answer headers fetch_resource returns (a renderer passes them on to the browser)
CORS_HEADERS: Final = ("access-control-allow-origin", "access-control-allow-credentials")

type Resolver = Callable[[str, int], Sequence[str]]


class _NoCookies(DefaultCookiePolicy):
    """A cookie policy that stores and returns nothing."""

    def set_ok(self, cookie: Cookie, request: Request) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: Request) -> bool:
        return False


def default_http_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    """The live HTTP client; see the module docstring."""
    return httpx2.Client(
        timeout=httpx2.Timeout(REQUEST_DEADLINE_S, connect=CONNECT_TIMEOUT_S),
        follow_redirects=False,
        trust_env=False,
        cookies=CookieJar(policy=_NoCookies()),
        limits=httpx2.Limits(max_keepalive_connections=0),
        headers={"User-Agent": USER_AGENT, "Connection": "close"},
        transport=transport,
    )


def default_resolver() -> Resolver:
    """The live resolver: every distinct TCP address ``socket.getaddrinfo`` gives, as text."""

    def resolve(host: str, port: int) -> tuple[str, ...]:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(str(info[4][0]) for info in infos))

    return resolve


def default_own_addresses() -> frozenset[IpAddress]:
    """This PC's own addresses: every address its host name resolves to locally."""
    found: set[IpAddress] = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
    except (OSError, UnicodeError):  # fail closed: without them, the own-address check is off
        raise AdapterError(
            False, FETCH_FAILED, "this PC's own addresses could not be read"
        ) from None
    for info in infos:
        try:
            found.add(ipaddress.ip_address(str(info[4][0])))
        except ValueError:
            continue
    return frozenset(found)


@dataclass(frozen=True, slots=True)
class _Kind:
    """What one kind of fetch meters, takes and accepts."""

    operation: str
    limit: int
    accepts: Callable[[str], bool]
    accept_header: str


@dataclass(frozen=True, slots=True)
class _Redirect:
    location: str


@dataclass(frozen=True, slots=True)
class _Answer:
    final_url: str
    status: int
    media_type: str
    charset: str | None
    body: bytes = field(repr=False)
    headers: tuple[tuple[str, str], ...] = ()  # the CORS_HEADERS the answer carried


@dataclass(frozen=True, slots=True)
class Resource:
    """One answer of ``fetch_resource``; its body never shows in the ``repr``."""

    final_url: str
    status: int
    media_type: str
    charset: str | None
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Failure:
    retryable: bool
    code: str
    message: str


def _site_of(host: str) -> str:
    """The registrable domain pacing keys ``host`` on: its own, else the host itself."""
    try:
        return registrable_domain(host)
    except IdentityError:
        return host


def _in_site(host: str, site: str) -> bool:
    try:
        return registrable_domain(host) == site
    except IdentityError:
        return False


def _is_text(media_type: str) -> bool:
    return media_type.startswith(TEXT_MEDIA_PREFIX)


def _is_page(media_type: str) -> bool:
    return media_type in PAGE_MEDIA_TYPES


def _allows(parser: Protego, url: str) -> bool:
    try:
        return bool(parser.can_fetch(url, ROBOTS_TOKEN))
    except Exception:  # noqa: BLE001 - a URL protego can't judge is not fetched
        return False


ROBOTS_KIND: Final = _Kind(ROBOTS, MAX_ROBOTS_BYTES, _is_text, ROBOTS_ACCEPT)
PAGE_KIND: Final = _Kind(PAGE, MAX_PAGE_BYTES, _is_page, PAGE_ACCEPT)


def check_target(url: str, site: str | None) -> FetchTarget:
    """Step 1 of a hop: ``url`` once the egress rules and the ``site`` pin accept it, else
    ``AdapterError`` ``fetch_refused`` naming the reason."""
    reason = url_refusal(url)
    if reason is not None:
        raise AdapterError(False, FETCH_REFUSED, f"the fetch was refused ({reason})")
    target = check_url(url)
    if site is not None and not _in_site(target.host, site):
        raise AdapterError(False, FETCH_REFUSED, f"the fetch was refused ({OFF_SITE})")
    return target


class HttpCrawler:
    """``CrawlerPort`` inside the egress filter; see the module docstring."""

    def __init__(
        self,
        *,
        meter: UsageMeterPort,
        http: httpx2.Client | None = None,
        resolve: Resolver | None = None,
        own_addresses: Collection[IpAddress] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._meter = meter
        self._http = http
        self._resolve = resolve
        self._own_addresses = None if own_addresses is None else frozenset(own_addresses)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pace_lock = threading.Lock()
        self._slots: dict[str, float] = {}

    # Live parts ---------------------------------------------------------------------------------

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def _resolver(self) -> Resolver:
        with self._lock:
            if self._resolve is None:
                self._resolve = default_resolver()  # looked up now, so tests can refuse it
            return self._resolve

    def _own(self) -> frozenset[IpAddress]:
        if self._own_addresses is not None:
            return self._own_addresses
        return default_own_addresses()  # looked up now, so tests can refuse it

    # The port -----------------------------------------------------------------------------------

    def fetch_robots(self, url: str, *, min_interval_s: float) -> Robots:
        interval = request_interval(min_interval_s)
        answer = self._fetch(url, site=None, interval=interval, kind=ROBOTS_KIND)
        if not 200 <= answer.status < 300:
            return Robots(final_url=answer.final_url, status=answer.status)
        text = answer.body.decode("utf-8-sig", errors="replace")
        try:
            parser = Protego.parse(text)
            delay = parser.crawl_delay(ROBOTS_TOKEN)
        except Exception:  # noqa: BLE001 - protego's own errors stay inside the adapter
            raise AdapterError(False, FETCH_FAILED, "robots.txt could not be parsed") from None
        return Robots(
            final_url=answer.final_url,
            status=answer.status,
            crawl_delay_s=None if delay is None else float(delay),
            allows=functools.partial(_allows, parser),
        )

    def fetch_page(self, url: str, *, site: str | None, min_interval_s: float) -> FetchedPage:
        interval = request_interval(min_interval_s)
        answer = self._fetch(url, site=site, interval=interval, kind=PAGE_KIND)
        if not 200 <= answer.status < 300:
            return FetchedPage(
                url=url, final_url=answer.final_url, status=answer.status, media_type=""
            )
        document = decode(answer.body, answer.charset)
        if answer.media_type == JSON_MEDIA_TYPE:
            return FetchedPage(
                url=url,
                final_url=answer.final_url,
                status=answer.status,
                media_type=answer.media_type,
                text=visible_text(document),
            )
        try:
            parsed = parse_html(document, answer.final_url)
        except Exception:  # noqa: BLE001 - the parser's own errors stay inside the adapter
            raise AdapterError(False, FETCH_FAILED, "the page could not be parsed") from None
        return FetchedPage(
            url=url,
            final_url=answer.final_url,
            status=answer.status,
            media_type=answer.media_type,
            text=parsed.text,
            links=parsed.links,
            html=document,
        )

    # For the renderer -----------------------------------------------------------------------------

    def fetch_resource(
        self,
        url: str,
        *,
        site: str | None,
        accepts: Callable[[str], bool],
        accept_header: str,
        operation: str,
        deadline: float | None,
        min_interval_s: float | None,
    ) -> Resource:
        """One resource inside the egress filter; see the module docstring."""
        interval = None if min_interval_s is None else request_interval(min_interval_s)
        kind = _Kind(operation, MAX_PAGE_BYTES, accepts, accept_header)
        answer = self._fetch(url, site=site, interval=interval, kind=kind, limit=deadline)
        return Resource(
            final_url=answer.final_url,
            status=answer.status,
            media_type=answer.media_type,
            charset=answer.charset,
            headers=MappingProxyType(dict(answer.headers)),
            body=answer.body,
        )

    # The hops -----------------------------------------------------------------------------------

    def _fetch(
        self,
        url: str,
        *,
        site: str | None,
        interval: float | None,
        kind: _Kind,
        limit: float | None = None,
    ) -> _Answer:
        """The hops of one fetch: paced at ``interval`` (``None``: unpaced), within the 30 s
        deadline and ``limit`` (a ``monotonic`` time, or ``None``)."""
        target = self._target(url, site)  # before any live part is built
        http = self._client()
        resolve = self._resolver()
        own = self._own()
        redirects = 0
        deadline: float | None = None  # set when the first request goes; covers every hop
        while True:
            self._check_deadline(limit if deadline is None else deadline)
            address = self._address(target.host, target.port, resolve, own)
            if interval is not None:
                self._pace(target.host, interval)
            if deadline is None:
                deadline = self._monotonic() + REQUEST_DEADLINE_S
                if limit is not None:
                    deadline = min(deadline, limit)
            self._check_deadline(deadline)
            with Metered(self._meter, PROVIDER, kind.operation):
                outcome = self._request(http, target, address, kind, deadline)
            if isinstance(outcome, _Answer):
                return outcome
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise AdapterError(
                    False, FETCH_TOO_MANY_REDIRECTS, f"more than {MAX_REDIRECTS} redirects"
                ) from None
            try:
                location = urllib.parse.urljoin(target.url, outcome.location.strip())
            except ValueError:
                raise AdapterError(
                    False, FETCH_REFUSED, f"the fetch was refused ({BAD_URL})"
                ) from None
            target = self._target(location, site)

    def _check_deadline(self, deadline: float | None) -> None:
        """Raise the retryable ``fetch_timeout`` once the fetch's deadline has passed."""
        if deadline is not None and self._monotonic() >= deadline:
            raise AdapterError(True, FETCH_TIMEOUT, "the request timed out") from None

    @staticmethod
    def _target(url: str, site: str | None) -> FetchTarget:
        """Step 1 of a hop: ``url`` once the egress rules and the ``site`` pin accept it."""
        return check_target(url, site)

    def _address(
        self, host: str, port: int, resolve: Resolver, own: frozenset[IpAddress]
    ) -> IpAddress:
        """The first address ``host`` resolves to, once every one it resolves to passes."""
        try:
            found = tuple(resolve(host, port))
        except (OSError, UnicodeError):
            raise AdapterError(False, FETCH_DNS_FAILED, "the host name did not resolve") from None
        except Exception:  # noqa: BLE001 - a resolver's own errors stay inside the adapter
            raise AdapterError(False, FETCH_FAILED, "the host name lookup failed") from None
        addresses: list[IpAddress] = []
        for text in found:
            try:
                addresses.append(ipaddress.ip_address(str(text)))
            except ValueError:
                raise AdapterError(
                    False, FETCH_DNS_FAILED, "the lookup returned something that isn't an address"
                ) from None
        if not addresses:
            raise AdapterError(False, FETCH_DNS_FAILED, "the host name did not resolve") from None
        for address in addresses:
            reason = address_refusal(address, own)
            if reason is not None:
                raise AdapterError(False, FETCH_REFUSED, f"the fetch was refused ({reason})")
        return addresses[0]

    def _pace(self, host: str, interval: float) -> None:
        """Wait for this request's slot: at least ``interval`` after the site's last one."""
        key = _site_of(host)
        with self._pace_lock:
            now = self._monotonic()
            last = self._slots.get(key)
            slot = now if last is None else max(now, last + interval)
            if len(self._slots) >= PACE_PRUNE_SIZE:
                self._slots = {
                    name: kept for name, kept in self._slots.items() if kept > now - PACE_MEMORY_S
                }
            self._slots[key] = slot
        if slot > now:
            self._sleep(slot - now)

    def _request(
        self,
        http: httpx2.Client,
        target: FetchTarget,
        address: IpAddress,
        kind: _Kind,
        deadline: float,
    ) -> _Answer | _Redirect:
        """The one GET of a hop, to ``address``; only ``AdapterError`` gets out."""
        default_port = DEFAULT_PORTS[target.scheme]
        host_header = target.host if target.port == default_port else f"{target.host}:{target.port}"
        headers = {
            "Host": host_header,
            "User-Agent": USER_AGENT,
            "Accept": kind.accept_header,
            "Connection": "close",
        }
        left = max(deadline - self._monotonic(), MIN_TIMEOUT_S)
        timeout = httpx2.Timeout(left, connect=min(CONNECT_TIMEOUT_S, left))
        outcome: _Answer | _Redirect | _Failure
        try:
            request_url = httpx2.URL(target.url).copy_with(host=str(address))
            with http.stream(
                "GET",
                request_url,
                headers=headers,
                extensions={"sni_hostname": target.host},
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                outcome = self._read(response, target.url, kind, deadline)
        except httpx2.TimeoutException:
            raise AdapterError(True, FETCH_TIMEOUT, "the request timed out") from None
        except httpx2.ConnectError:
            raise AdapterError(
                False, FETCH_CONNECT_FAILED, "nothing answered at the host's address"
            ) from None
        except Exception:  # noqa: BLE001 - every other failure of the request
            raise AdapterError(False, FETCH_FAILED, "the request failed") from None
        if isinstance(outcome, _Failure):
            raise AdapterError(outcome.retryable, outcome.code, outcome.message) from None
        return outcome

    def _read(
        self,
        response: httpx2.Response,
        logical_url: str,
        kind: _Kind,
        deadline: float,
    ) -> _Answer | _Redirect | _Failure:
        status = response.status_code
        if not MIN_STATUS <= status <= MAX_STATUS:
            return _Failure(False, FETCH_FAILED, "the answer's status is not an HTTP status")
        location = response.headers.get("location")
        if status in REDIRECT_STATUSES and location:
            return _Redirect(location)
        headers = tuple(
            (name, value)
            for name in CORS_HEADERS
            if (value := response.headers.get(name)) is not None
        )
        if not 200 <= status < 300:
            return _Answer(logical_url, status, "", None, b"", headers)
        media_type, charset = content_type(response.headers.get("content-type"))
        if not kind.accepts(media_type):
            return _Failure(False, FETCH_UNSUPPORTED_TYPE, "the answer's media type isn't taken")
        declared = response.headers.get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > kind.limit:
            return _Failure(False, FETCH_TOO_LARGE, f"the answer is over {kind.limit} bytes")
        body = bytearray()
        if self._monotonic() > deadline:
            return _Failure(True, FETCH_TIMEOUT, "the request timed out")
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > kind.limit:
                return _Failure(False, FETCH_TOO_LARGE, f"the answer is over {kind.limit} bytes")
            if self._monotonic() > deadline:
                return _Failure(True, FETCH_TIMEOUT, "the request timed out")
        return _Answer(logical_url, status, media_type, charset, bytes(body), headers)

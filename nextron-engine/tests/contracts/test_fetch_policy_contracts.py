"""Fetch-policy contracts (FR-49, AD-9), each with a live proof: every adapter that fetches the
web obeys the denylist on its first URL and on every redirect.

- **Every fetching adapter is listed.** ``FETCHING_ADAPTERS`` holds a driver for every built
  adapter package among ``FETCHING_PACKAGES`` (``crawler`` and ``renderer`` today; ``ats``,
  Story 2.11, once it is built). A package is built once it has an ``__init__.py``; the check
  fails until its driver is added here. A driver builds the adapter on a ``Recorder`` (an
  ``httpx2.MockTransport`` and a resolver that record what they are asked) and returns each of
  its fetching calls, by name. The renderer's driver is a ``PlaywrightRenderer`` on the
  recorder's ``HttpCrawler`` and the fake browser of ``tests/fakes/browser.py``: the page's
  main document goes through the route handler, so its first URL and every redirect are
  judged as the crawler's are.
- **A denylisted first URL** (LinkedIn, Instagram, a Google Maps host or path, a job site) is
  refused with ``AdapterError`` code ``fetch_refused`` naming ``denylisted``, with no host name
  looked up and no request sent.
- **A redirect to a denylisted host** is refused the same way after exactly one request: the one
  that answered with the redirect.
- **A private address** is never requested: a first URL whose host resolves to a private address
  is refused (``fetch_refused``) after its lookup with no request, and a redirect to such a host
  after exactly one request (none to it).

Nothing here reaches the network. The proofs run the same checks on a probe adapter that follows
redirects without the policy.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

import httpx2

from nextron.adapters.crawler import HttpCrawler
from nextron.adapters.renderer import PlaywrightRenderer
from nextron.ports.errors import AdapterError
from tests.contracts.support import PACKAGE
from tests.fakes.browser import FakeBrowsers
from tests.fakes.usage_meter import InMemoryUsageMeter

FETCHING_PACKAGES: Final = ("crawler", "renderer", "ats")
FIRST_HOST: Final = "smith-law.co.uk"
FIRST_URL: Final = f"https://{FIRST_HOST}/robots.txt"
PUBLIC: Final = "93.184.216.34"
PRIVATE_HOST: Final = "intranet.smith-law.co.uk"  # its lookup answers PRIVATE
PRIVATE: Final = "10.0.0.5"
PRIVATE_URL: Final = f"https://{PRIVATE_HOST}/robots.txt"

DENIED_FIRST_URLS: Final = (
    "https://www.linkedin.com/company/smith-law",
    "https://lnkd.in/abc",
    "https://www.instagram.com/smithlaw/",
    "https://maps.google.com/?q=smith+law",
    "https://www.google.com/maps/place/smith-law",
    "https://maps.app.goo.gl/AbCd",
    "https://g.page/smith-law",
    "https://uk.indeed.com/cmp/smith-law",
    "https://www.glassdoor.co.uk/Overview/x",
    "https://www.ziprecruiter.com/c/smith",
    "https://www.reed.co.uk/jobs/x",
    "https://www.totaljobs.com/company/x",
    "https://www.cv-library.co.uk/company/x",
)
DENIED_REDIRECTS: Final = (
    "https://maps.google.com/?q=smith",
    "https://www.maps.google.co.uk/place/x",
    "https://www.google.com/maps/place/x",
    "https://www.linkedin.com/company/smith-law",
    "https://www.instagram.com/smithlaw/",
    "https://uk.indeed.com/jobs",
)


@dataclass
class Recorder:
    """The fake web and DNS a driver builds its adapter on: every request redirects to
    ``location``, and every lookup answers a public address (``PRIVATE_HOST``'s a private
    one)."""

    location: str = ""
    requests: list[httpx2.Request] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)

    def transport(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(302, headers={"location": self.location})

    def resolve(self, host: str, port: int) -> Sequence[str]:
        self.lookups.append(host)
        return (PRIVATE,) if host == PRIVATE_HOST else (PUBLIC,)

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.transport))


type Fetch = Callable[[str], object]
type Driver = Callable[[Recorder], Sequence[tuple[str, Fetch]]]


def recorded_crawler(recorder: Recorder) -> HttpCrawler:
    return HttpCrawler(
        meter=InMemoryUsageMeter(),
        http=recorder.client(),
        resolve=recorder.resolve,
        own_addresses=frozenset(),
        sleep=lambda seconds: None,
    )


def crawler_driver(recorder: Recorder) -> Sequence[tuple[str, Fetch]]:
    crawler = recorded_crawler(recorder)
    return (
        ("fetch_robots", lambda url: crawler.fetch_robots(url, min_interval_s=1.0)),
        ("fetch_page", lambda url: crawler.fetch_page(url, site=None, min_interval_s=1.0)),
        (
            "fetch_resource",
            lambda url: crawler.fetch_resource(
                url,
                site=None,
                accepts=lambda media_type: True,
                accept_header="*/*",
                operation="render",
                deadline=None,
                min_interval_s=None,
            ),
        ),
    )


def renderer_driver(recorder: Recorder) -> Sequence[tuple[str, Fetch]]:
    renderer = PlaywrightRenderer(
        meter=InMemoryUsageMeter(), fetcher=recorded_crawler(recorder), browser=FakeBrowsers()
    )
    return (("render", lambda url: renderer.render(url, site=None, min_interval_s=1.0)),)


# Every built fetching adapter package, with its driver.
FETCHING_ADAPTERS: Final[dict[str, Driver]] = {
    "crawler": crawler_driver,
    "renderer": renderer_driver,
}


# The checks -------------------------------------------------------------------------------------


def built_fetching_packages() -> set[str]:
    """The packages of ``FETCHING_PACKAGES`` that exist under ``nextron/adapters``."""
    adapters = PACKAGE / "adapters"
    return {name for name in FETCHING_PACKAGES if (adapters / name / "__init__.py").is_file()}


def _fetches(driver: Driver, location: str = "") -> list[tuple[Recorder, str, Fetch]]:
    """Each fetching call of ``driver``, each on a fresh ``Recorder``."""
    found = []
    for index in range(len(driver(Recorder()))):
        recorder = Recorder(location)
        name, fetch = driver(recorder)[index]
        found.append((recorder, name, fetch))
    return found


def _refusal(name: str, url: str, fetch: Fetch, reason: str = "denylisted") -> str | None:
    """Why calling ``fetch(url)`` isn't a refusal naming ``reason``, or ``None``."""
    try:
        fetch(url)
    except AdapterError as error:
        if error.code != "fetch_refused" or reason not in error.message:
            return f"{name} {url}: refused as {error.code} ({error.message})"
        return None
    return f"{name} {url}: not refused"


def first_url_problems(driver: Driver) -> list[str]:
    """Each call that doesn't refuse a denylisted first URL before any lookup or request."""
    problems = []
    for url in DENIED_FIRST_URLS:
        for recorder, name, fetch in _fetches(driver):
            problem = _refusal(name, url, fetch)
            if problem is not None:
                problems.append(problem)
            if recorder.lookups or recorder.requests:
                problems.append(f"{name} {url}: looked up or requested")
    return problems


def redirect_problems(driver: Driver) -> list[str]:
    """Each call that doesn't refuse a redirect to a denylisted host after exactly one
    request."""
    problems = []
    for location in DENIED_REDIRECTS:
        for recorder, name, fetch in _fetches(driver, location):
            problem = _refusal(name, FIRST_URL, fetch)
            if problem is not None:
                problems.append(problem)
            if len(recorder.requests) != 1:
                problems.append(f"{name} {location}: {len(recorder.requests)} requests")
    return problems


def private_problems(driver: Driver) -> list[str]:
    """Each call that requests a host resolving to a private address, as a first URL or after a
    redirect."""
    problems = []
    for recorder, name, fetch in _fetches(driver):
        problem = _refusal(name, PRIVATE_URL, fetch, "private")
        if problem is not None:
            problems.append(problem)
        if recorder.requests:
            problems.append(f"{name} {PRIVATE_URL}: {len(recorder.requests)} requests")
    for recorder, name, fetch in _fetches(driver, PRIVATE_URL):
        problem = _refusal(name, FIRST_URL, fetch, "private")
        if problem is not None:
            problems.append(problem)
        if len(recorder.requests) != 1:
            problems.append(f"{name} redirect to {PRIVATE_URL}: {len(recorder.requests)} requests")
    return problems


# The contracts ----------------------------------------------------------------------------------


def test_every_built_fetching_adapter_has_a_driver() -> None:
    assert {"crawler", "renderer"} <= built_fetching_packages()
    assert set(FETCHING_ADAPTERS) == built_fetching_packages()


def test_every_fetching_adapter_refuses_a_denylisted_first_url_before_any_lookup() -> None:
    for package, driver in FETCHING_ADAPTERS.items():
        assert first_url_problems(driver) == [], package


def test_every_fetching_adapter_refuses_a_denylisted_redirect_after_one_request() -> None:
    for package, driver in FETCHING_ADAPTERS.items():
        assert redirect_problems(driver) == [], package


def test_every_fetching_adapter_never_requests_a_private_address() -> None:
    for package, driver in FETCHING_ADAPTERS.items():
        assert private_problems(driver) == [], package


# The proofs -------------------------------------------------------------------------------------


def naive_driver(recorder: Recorder) -> Sequence[tuple[str, Fetch]]:
    """A probe adapter that fetches any URL and follows one redirect without the policy."""

    def fetch(url: str) -> object:
        client = recorder.client()
        first = client.get(url)
        location = first.headers.get("location")
        return client.get(location) if location else first

    return (("fetch", fetch),)


def wrong_code_driver(recorder: Recorder) -> Sequence[tuple[str, Fetch]]:
    """A probe adapter that looks the host up, then refuses with another code."""

    def fetch(url: str) -> object:
        recorder.resolve(url, 443)
        raise AdapterError(False, "fetch_failed", "no")

    return (("fetch", fetch),)


def test_the_checks_catch_an_adapter_that_ignores_the_denylist() -> None:
    first = first_url_problems(naive_driver)
    redirects = redirect_problems(naive_driver)

    assert len(first) == 2 * len(DENIED_FIRST_URLS)  # not refused, and requested
    assert first[:2] == [
        f"fetch {DENIED_FIRST_URLS[0]}: not refused",
        f"fetch {DENIED_FIRST_URLS[0]}: looked up or requested",
    ]
    assert redirects[:2] == [
        f"fetch {FIRST_URL}: not refused",
        f"fetch {DENIED_REDIRECTS[0]}: 2 requests",
    ]


def test_the_private_check_catches_an_adapter_that_resolves_nothing_itself() -> None:
    problems = private_problems(naive_driver)

    assert problems == [
        f"fetch {PRIVATE_URL}: not refused",
        f"fetch {PRIVATE_URL}: 1 requests",
        f"fetch {FIRST_URL}: not refused",
        f"fetch redirect to {PRIVATE_URL}: 2 requests",
    ]


def test_the_checks_catch_a_refusal_with_another_code_after_a_lookup() -> None:
    problems = first_url_problems(wrong_code_driver)

    assert problems[:2] == [
        f"fetch {DENIED_FIRST_URLS[0]}: refused as fetch_failed (no)",
        f"fetch {DENIED_FIRST_URLS[0]}: looked up or requested",
    ]

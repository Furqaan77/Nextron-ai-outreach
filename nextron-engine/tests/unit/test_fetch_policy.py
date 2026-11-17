"""``nextron.domain.fetch_policy`` (FR-49, AD-9): the denylist of URLs the engine never fetches
and the egress rules every fetching adapter applies to each hop.

The denylist (Story 2.3, extended by Story 2.5): LinkedIn, Instagram, Google Maps hosts (a
``maps`` label anywhere left of ``google.*``) and ``/maps`` paths, ``maps.app.goo.gl``,
``g.page``, ``indeed.*``, ``glassdoor.*``, ``ziprecruiter.com``, ``reed.co.uk``,
``totaljobs.com`` and ``cv-library.co.uk``; near misses that are other organizations' sites;
and failing closed on a URL whose host can't be read.

The egress rules (Story 2.5): ``check_url``'s targets and its refusals in their order, a refusal
whose message is the reason and never the URL, and ``address_refusal``'s reasons for every
refused range and this PC's own addresses."""

import ipaddress

import pytest

from nextron.domain import fetch_policy
from nextron.domain.errors import DomainError
from nextron.domain.fetch_policy import (
    ADDRESS_REASONS,
    DENIED_BRANDS,
    DENIED_DOMAINS,
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    MAX_ROBOTS_BYTES,
    PAGE_MEDIA_TYPES,
    URL_REASONS,
    FetchRefused,
    FetchTarget,
    address_refusal,
    check_url,
    is_denied,
    url_refusal,
)

# The denylist ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # LinkedIn and its subdomains, and its short links
        "linkedin.com",
        "https://www.linkedin.com/company/x",
        "https://uk.linkedin.com/in/jane-doe",
        "HTTPS://WWW.LINKEDIN.COM/in/x",
        "linkedin.com.",
        "lnkd.in/abc",
        # Instagram
        "instagram.com/firm",
        "https://www.instagram.com/p/x",
        "instagr.am/p/x",
        # job sites
        "ziprecruiter.com",
        "https://www.ziprecruiter.com/jobs/x",
        "indeed.com",
        "uk.indeed.com/jobs",
        "https://www.indeed.co.uk/viewjob?jk=1",
        "indeed.de",
        "glassdoor.com/Reviews/x",
        "glassdoor.co.uk",
        "https://www.glassdoor.ie/x",
        # Google Maps hosts
        "maps.google.com",
        "https://maps.google.co.uk/?q=x",
        "maps.app.goo.gl/AbCd",
        "https://maps.app.goo.gl/",
        # /maps paths
        "https://www.google.com/maps/place/x",
        "google.com/maps",
        "google.co.uk/maps/",
        "https://www.google.com/MAPS/place/x",
        "https://www.google.com/%6Daps/place/x",  # percent-decoded before matching
        "goo.gl/maps/xyz",
        # paths normalised before the /maps check
        "google.com//maps/x",
        "https://www.google.com///maps",
        "google.com/maps;x",
        "google.com/maps;x/place",
        "goo.gl//maps/x",
        "google.com/./maps",
        "google.com/x/../maps/place",
        "google.com/../maps",
        "google.com/%2Fmaps/x",
    ],
)
def test_denied(url: str) -> None:
    assert is_denied(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.smith-law.co.uk/team",
        "notlinkedin.com",
        "linkedin.com.example.org",
        "mylinkedin.co",
        "linkedin.co.uk",  # not linkedin.com; another registrant
        "instagramfirm.com",
        "indeedjobs.com",
        "glassdoors.com",
        "notindeed.co.uk",
        "ziprecruiter.org",
        "google.com/mapsearch",
        "google.com/mapsx/y",
        "https://www.google.com/search?q=maps",
        "google.com/x/maps",
        "google.com/maps/../search",
        "google.com/x;maps",
        "goo.gl/abc",
        "maps.example.com",
        "maps.googlex.com",
        "app.goo.gl.example.com",
        "x.blogspot.com/maps/x",
    ],
)
def test_not_denied(url: str) -> None:
    assert not is_denied(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "ftp://linkedin.com",
        "10.0.0.1",
        "https://[::1]/",
        "a..b",
        "straße.de",
        "x" * 2049,
    ],
)
def test_a_url_whose_host_cant_be_read_is_denied(url: str) -> None:
    assert is_denied(url)


def test_a_host_with_no_registrable_domain_is_matched_by_its_host_only() -> None:
    assert not is_denied("localhost")  # readable, no registrable domain, not listed
    assert not is_denied("co.uk/maps")  # a public suffix: no registrable domain to be google


def test_a_non_str_raises_type_error() -> None:
    with pytest.raises(TypeError):
        is_denied(b"linkedin.com")  # type: ignore[arg-type]


def test_the_lists() -> None:
    assert DENIED_DOMAINS == (
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
    )
    assert DENIED_BRANDS == {"indeed", "glassdoor"}
    assert "2.5" in (fetch_policy.__doc__ or "")


# Story 2.5's additions to the denylist ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "reed.co.uk",
        "https://www.reed.co.uk/jobs/x",
        "totaljobs.com",
        "https://www.totaljobs.com/job/1",
        "cv-library.co.uk/job/2",
        "https://www.cv-library.co.uk/",
        "g.page/r/abc",
        "https://g.page/smith-law/review",
        # a maps label anywhere left of a google.* registrable domain
        "www.maps.google.com",
        "https://www.maps.google.co.uk/x",
        "a.maps.b.google.com",
        "https://MAPS.Google.de/",
    ],
)
def test_story_2_5_denies_more_job_sites_g_page_and_every_maps_host(url: str) -> None:
    assert is_denied(url)


@pytest.mark.parametrize(
    "url",
    [
        "notreed.co.uk",
        "reed.com",
        "mytotaljobs.com",
        "cv-library.com",
        "g.pages.dev",
        "page.g.example.com",
        "maps.notgoogle.com",
        "www.google.com/search",
        "mapsx.google.com",  # the label must be exactly maps
        "google.maps.example.com",
    ],
)
def test_story_2_5_near_misses_are_not_denied(url: str) -> None:
    assert not is_denied(url)


# check_url --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "target"),
    [
        (
            "https://www.smith-law.co.uk/team?page=2#people",
            FetchTarget(
                "https://www.smith-law.co.uk/team?page=2", "https", "www.smith-law.co.uk", 443,
                "/team",
            ),
        ),
        (
            "HTTP://Smith-Law.co.uk",
            FetchTarget("http://smith-law.co.uk/", "http", "smith-law.co.uk", 80, "/"),
        ),
        (  # a default port is left out, so the page is one URL with or without it
            "https://smith-law.co.uk:443/about/",
            FetchTarget(
                "https://smith-law.co.uk/about/", "https", "smith-law.co.uk", 443, "/about/"
            ),
        ),
        (
            "http://smith-law.co.uk:80?x",
            FetchTarget("http://smith-law.co.uk/?x", "http", "smith-law.co.uk", 80, "/"),
        ),
        (  # another scheme's default port is kept
            "http://smith-law.co.uk:443/x",
            FetchTarget("http://smith-law.co.uk:443/x", "http", "smith-law.co.uk", 443, "/x"),
        ),
        (
            "https://bücher.de/über",
            FetchTarget("https://xn--bcher-kva.de/über", "https", "xn--bcher-kva.de", 443, "/über"),
        ),
        (
            "https://www.example.com./x",
            FetchTarget("https://www.example.com/x", "https", "www.example.com", 443, "/x"),
        ),
    ],
)  # fmt: skip
def test_check_url_splits_an_accepted_url(url: str, target: FetchTarget) -> None:
    assert check_url(url) == target
    assert url_refusal(url) is None


LONG = "https://smith-law.co.uk/" + "x" * 2030


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        # bad_url
        ("", "bad_url"),
        (LONG, "bad_url"),
        ("https://smith-law.co.uk/a b", "bad_url"),
        (" https://smith-law.co.uk/", "bad_url"),
        ("https://smith-law.co.uk/\x00", "bad_url"),
        ("https://smith-law.co.uk/\u200b", "bad_url"),
        ("https://[::1/", "bad_url"),
        ("https:///path", "bad_url"),
        ("https://smith-law.co.uk:99999/", "bad_url"),
        ("https://smith-law.co.uk:http/", "bad_url"),
        ("https://a..b.com/", "bad_url"),
        ("https://stra\u00dfe.de/", "bad_url"),
        # scheme
        ("ftp://smith-law.co.uk/", "scheme"),
        ("smith-law.co.uk/team", "scheme"),
        ("//smith-law.co.uk/team", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("file:///C:/Windows/win.ini", "scheme"),
        ("mailto:jane@smith-law.co.uk", "scheme"),
        # userinfo
        ("https://jane@smith-law.co.uk/", "userinfo"),
        ("https://jane:pw@smith-law.co.uk/", "userinfo"),
        ("https://@smith-law.co.uk/", "userinfo"),
        # port
        ("https://smith-law.co.uk:8080/", "port"),
        ("http://smith-law.co.uk:22/", "port"),
        ("https://smith-law.co.uk:0/", "port"),
        # ip_host
        ("https://10.0.0.1/", "ip_host"),
        ("http://93.184.216.34/", "ip_host"),
        ("https://[::1]/", "ip_host"),
        ("https://[2606:4700::1111]/", "ip_host"),
        ("http://127.1/", "ip_host"),
        ("http://0x7f.1/", "ip_host"),
        ("http://2130706433/", "ip_host"),
        ("http://127.0.0.1./", "ip_host"),
        # denylisted
        ("https://www.linkedin.com/in/x", "denylisted"),
        ("https://maps.google.com/", "denylisted"),
        ("https://www.google.com/maps/place/x", "denylisted"),
        ("https://uk.indeed.com/jobs", "denylisted"),
    ],
)
def test_check_url_refuses_with_its_reason(url: str, reason: str) -> None:
    assert url_refusal(url) == reason
    with pytest.raises(FetchRefused) as refused:
        check_url(url)
    assert refused.value.reason == reason
    assert str(refused.value) == reason  # the reason, never the URL
    assert refused.value.code == "fetch_refused"
    assert isinstance(refused.value, DomainError)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://jane@smith-law.co.uk:8080/", "scheme"),  # scheme before userinfo and port
        ("https://jane@10.0.0.1:8080/", "userinfo"),  # userinfo before port and ip_host
        ("https://10.0.0.1:8080/", "port"),  # port before ip_host
        ("https://www.linkedin.com:8080/", "port"),  # port before denylisted
        ("https://jane@www.linkedin.com/", "userinfo"),
    ],
)
def test_check_url_reports_the_first_reason_in_order(url: str, reason: str) -> None:
    assert url_refusal(url) == reason


def test_the_reasons_and_limits() -> None:
    assert URL_REASONS == ("bad_url", "scheme", "userinfo", "port", "ip_host", "denylisted")
    assert ADDRESS_REASONS == (
        "unspecified",
        "loopback",
        "link_local",
        "multicast",
        "cgnat",
        "private",
        "ula",
        "ipv4_embedded",
        "not_global",
        "own_address",
    )
    assert (MAX_REDIRECTS, MAX_PAGE_BYTES, MAX_ROBOTS_BYTES) == (5, 5 * 1024 * 1024, 512 * 1024)
    assert PAGE_MEDIA_TYPES == {"text/html", "application/xhtml+xml", "application/json"}


def test_check_url_takes_a_str_only() -> None:
    with pytest.raises(TypeError):
        check_url(b"https://smith-law.co.uk/")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        url_refusal(None)  # type: ignore[arg-type]


# address_refusal --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("0.0.0.0", "unspecified"),
        ("::", "unspecified"),
        ("127.0.0.1", "loopback"),
        ("127.255.0.9", "loopback"),
        ("::1", "loopback"),
        ("169.254.169.254", "link_local"),  # the cloud metadata address
        ("fe80::1", "link_local"),
        ("fe80::1%3", "link_local"),
        ("224.0.0.1", "multicast"),
        ("ff02::1", "multicast"),
        ("100.64.0.1", "cgnat"),
        ("100.127.255.254", "cgnat"),
        ("10.0.0.1", "private"),
        ("172.16.5.4", "private"),
        ("172.31.255.255", "private"),
        ("192.168.1.1", "private"),
        ("fc00::1", "ula"),
        ("fd12:3456::1", "ula"),
        ("::ffff:10.0.0.1", "ipv4_embedded"),
        ("::ffff:8.8.8.8", "ipv4_embedded"),
        ("::8.8.8.8", "ipv4_embedded"),
        ("2002:808:808::1", "ipv4_embedded"),
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "ipv4_embedded"),
        ("64:ff9b::808:808", "ipv4_embedded"),
        ("64:ff9b:1::1", "ipv4_embedded"),
        ("192.0.2.1", "not_global"),
        ("198.18.0.1", "not_global"),
        ("240.0.0.1", "not_global"),
        ("255.255.255.255", "not_global"),
        ("2001:db8::1", "not_global"),
    ],
)
def test_address_refusal_names_each_refused_range(address: str, reason: str) -> None:
    assert address_refusal(ipaddress.ip_address(address), ()) == reason


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700::1111"])
def test_a_public_address_passes_unless_it_is_this_pcs_own(address: str) -> None:
    found = ipaddress.ip_address(address)

    assert address_refusal(found, ()) is None
    assert address_refusal(found, {found}) == "own_address"
    assert address_refusal(found, frozenset({ipaddress.ip_address("1.1.1.1")})) is None


def test_an_own_address_is_compared_without_its_scope() -> None:
    own = ipaddress.ip_address("2606:4700::1111%7")

    assert address_refusal(ipaddress.ip_address("2606:4700::1111"), [own]) == "own_address"


def test_address_refusal_takes_an_ip_address_only() -> None:
    with pytest.raises(TypeError):
        address_refusal("93.184.216.34", ())  # type: ignore[arg-type]

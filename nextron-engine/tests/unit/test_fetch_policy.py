"""``nextron.domain.fetch_policy`` (FR-49): the denylist of URLs the engine never fetches.
Story 2.5's acceptance hosts (LinkedIn, Instagram, Google Maps hosts and ``/maps`` paths,
``maps.app.goo.gl``, ``indeed.*``, ``glassdoor.*``, ``ziprecruiter.com``), near misses that are
other organizations' sites, and failing closed on a URL whose host can't be read."""

import pytest

from nextron.domain import fetch_policy
from nextron.domain.fetch_policy import DENIED_BRANDS, DENIED_DOMAINS, is_denied


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
    )
    assert DENIED_BRANDS == {"indeed", "glassdoor"}
    assert "2.5" in (fetch_policy.__doc__ or "")

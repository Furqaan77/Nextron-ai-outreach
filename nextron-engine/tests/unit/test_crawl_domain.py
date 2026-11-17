"""``nextron.domain.crawl`` (FR-10, AD-18, NFR-6): the crawler's identity, link classification
by whole-token keywords, the outcomes and their labels, the request interval, the visible-text
normalisation and its cap, the text hash, the retryable codes and the crawl jobs' keys."""

import hashlib
import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain.crawl import (
    HTML_MEDIA_TYPES,
    KEYWORDS,
    LINK_TAGS,
    MAX_DEPTH,
    MAX_STORED_TEXT,
    OUTCOME_LABELS,
    ROBOTS_TOKEN,
    RUN_DEADLINE,
    TEXT_RETENTION,
    USER_AGENT,
    CrawlOutcome,
    PageKind,
    classify_link,
    crawl_interval,
    http_code,
    is_retryable,
    lead_job_keys,
    request_interval,
    text_hash,
    visible_text,
)


def test_the_crawler_names_itself() -> None:
    assert USER_AGENT == "NextronBot/1.0 (+https://nextronai.com)"
    assert ROBOTS_TOKEN == "NextronBot"


def test_the_kinds_outcomes_and_constants() -> None:
    assert [str(kind) for kind in PageKind] == [
        "home",
        "team",
        "about",
        "careers",
        "contact",
        "services",
        "news",
    ]
    assert [str(outcome) for outcome in CrawlOutcome] == [
        "ok",
        "unreachable",
        "robots_disallowed",
        "lead_merged",
    ]
    assert OUTCOME_LABELS[CrawlOutcome.UNREACHABLE] == "website unreachable"
    assert OUTCOME_LABELS[CrawlOutcome.ROBOTS_DISALLOWED] == (
        "website not crawled: its robots.txt disallows it or could not be read"
    )
    assert set(OUTCOME_LABELS) == set(CrawlOutcome)
    assert (TEXT_RETENTION, RUN_DEADLINE, MAX_DEPTH) == (
        timedelta(days=30),
        timedelta(minutes=10),
        2,
    )
    assert MAX_STORED_TEXT == 100_000
    assert LINK_TAGS == ("a", "iframe", "script")
    assert HTML_MEDIA_TYPES == {"text/html", "application/xhtml+xml"}
    assert PageKind.HOME not in KEYWORDS


# classify_link ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "text", "kind"),
    [
        ("/team", "", PageKind.TEAM),
        ("/our-people/", "", PageKind.TEAM),
        ("/x", "Our People", PageKind.TEAM),
        ("/staff", "", PageKind.TEAM),
        ("/attorneys/jane-doe", "", PageKind.TEAM),
        ("/lawyers", "", PageKind.TEAM),
        ("/solicitors", "", PageKind.TEAM),
        ("/about-us", "", PageKind.ABOUT),
        ("/x", "Who we are", PageKind.ABOUT),
        ("/careers", "", PageKind.CAREERS),
        ("/career/apply", "", PageKind.CAREERS),
        ("/jobs", "", PageKind.CAREERS),
        ("/vacancies", "", PageKind.CAREERS),
        ("/x", "Join us", PageKind.CAREERS),
        ("/contact", "", PageKind.CONTACT),
        ("/offices/london", "", PageKind.CONTACT),
        ("/locations", "", PageKind.CONTACT),
        ("/services", "", PageKind.SERVICES),
        ("/practice-areas/", "", PageKind.SERVICES),
        ("/x", "Practice areas", PageKind.SERVICES),
        ("/expertise", "", PageKind.SERVICES),
        ("/what-we-do", "", PageKind.SERVICES),
        ("/news", "", PageKind.NEWS),
        ("/press/2026", "", PageKind.NEWS),
        ("/blog/a-post", "", PageKind.NEWS),
        ("/insights", "", PageKind.NEWS),
        ("/TEAM", "", PageKind.TEAM),  # any case
        ("/our%20people", "", PageKind.TEAM),  # percent-decoded
        ("/x", "  Meet the TEAM ", PageKind.TEAM),
    ],
)
def test_a_keyword_in_the_path_or_the_anchor_text_classifies_a_link(
    path: str, text: str, kind: PageKind
) -> None:
    assert classify_link(path, text) is kind


@pytest.mark.parametrize(
    ("path", "text", "kind"),
    [
        ("/about/team", "", PageKind.TEAM),  # within one, the first kind in order
        ("/careers", "Join our team", PageKind.CAREERS),  # the path decides when it matches
        ("/x", "News about our team", PageKind.TEAM),
        ("/page", "Contact", PageKind.CONTACT),  # else the anchor text
    ],
)
def test_the_path_wins_then_the_order_of_the_kinds(path: str, text: str, kind: PageKind) -> None:
    assert classify_link(path, text) is kind


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("/teamwork", ""),  # whole tokens only
        ("/steam", ""),
        ("/aboutface", ""),
        ("/newsletter", ""),
        ("/contactless", ""),
        ("/whoweare", ""),
        ("/", ""),
        ("/x", "Read more"),
        ("/joins/usual", ""),
    ],
)
def test_partial_words_dont_match(path: str, text: str) -> None:
    assert classify_link(path, text) is None


def test_people_first_is_team_because_people_is_a_whole_token() -> None:
    assert classify_link("/our", "people-first") is PageKind.TEAM


@pytest.mark.parametrize(
    "path",
    [
        "/team/brochure.pdf",
        "/careers/photo.JPG",
        "/about/report.docx",
        "/news/archive.zip",
        "/team/intro.mp4",
        "/services/deck.pptx",
        "/team/logo.svg",
        "/news/podcast.mp3",
        "/team/list.csv;v=1",
    ],
)
def test_a_non_html_file_never_matches(path: str) -> None:
    assert classify_link(path, "Our team") is None


@pytest.mark.parametrize("path", ["/team.html", "/careers.php", "/about.aspx", "/news/"])
def test_an_html_page_extension_still_matches(path: str) -> None:
    assert classify_link(path, "") is not None


def test_classify_link_takes_str_only() -> None:
    with pytest.raises(TypeError):
        classify_link(b"/team", "")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        classify_link("/team", None)  # type: ignore[arg-type]


# Timing -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delay", "interval"),
    [
        (None, 1.0),
        (0, 1.0),
        (0.5, 1.0),
        (-3, 1.0),
        (1, 1.0),
        (3, 3.0),
        (2.5, 2.5),
        (10, 10.0),
        (60, 10.0),
        (math.inf, 10.0),
        (math.nan, 1.0),
    ],
)
def test_crawl_interval_is_the_delay_within_1_to_10_seconds(
    delay: float | None, interval: float
) -> None:
    assert crawl_interval(delay) == interval


@pytest.mark.parametrize(
    ("given", "interval"), [(0, 1.0), (0.2, 1.0), (1, 1.0), (4.5, 4.5), (math.nan, 1.0)]
)
def test_request_interval_never_goes_under_one_second(given: float, interval: float) -> None:
    assert request_interval(given) == interval


@pytest.mark.parametrize("bad", ["1", True, [1]])
def test_the_intervals_take_numbers_only(bad: object) -> None:
    with pytest.raises(TypeError):
        request_interval(bad)  # type: ignore[arg-type]
    if not isinstance(bad, bool):
        with pytest.raises(TypeError):
            crawl_interval(bad)  # type: ignore[arg-type]


# Text -------------------------------------------------------------------------------------------


def test_visible_text_removes_format_characters_and_normalises_lines() -> None:
    raw = (
        "\ufeff  Smith\u00ad Law   LLP \n"
        "\n"
        "\t \u00a0\n"
        "Our   in\u200bnovation\u200d \u2066team\u2069\r\n"
        "   last\tline   "
    )

    assert visible_text(raw) == "Smith Law LLP\nOur innovation team\nlast line"


def test_visible_text_is_idempotent_and_empty_stays_empty() -> None:
    once = visible_text("  a  b \n\n c \u00ad")

    assert visible_text(once) == once == "a b\nc"
    assert visible_text("") == ""
    assert visible_text(" \n\u200b\n ") == ""


def test_visible_text_is_capped_at_a_line_boundary() -> None:
    line = "x" * 999
    raw = "\n".join([line] * 200)  # 200,000 characters with the breaks

    text = visible_text(raw)

    assert len(text) <= MAX_STORED_TEXT
    assert text == "\n".join([line] * 100)  # 100 lines and 99 breaks: 99,999 characters
    assert visible_text("y" * 150_000) == "y" * MAX_STORED_TEXT  # one long line is cut


def test_text_hash_is_the_sha256_of_the_utf8() -> None:
    assert text_hash("Smith Law") == hashlib.sha256(b"Smith Law").hexdigest()
    assert text_hash("bücher") == hashlib.sha256("bücher".encode()).hexdigest()
    with pytest.raises(TypeError):
        text_hash(b"x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        visible_text(None)  # type: ignore[arg-type]


# Retries and jobs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("fetch_timeout", True),
        ("http_500", True),
        ("http_503", True),
        ("http_599", True),
        ("http_404", False),
        ("http_429", False),
        ("http_5000", False),
        ("fetch_dns_failed", False),
        ("fetch_connect_failed", False),
        ("fetch_refused", False),
        ("fetch_failed", False),
        (None, False),
    ],
)
def test_only_timeouts_and_5xx_are_retryable(code: str | None, retryable: bool) -> None:
    assert is_retryable(code) is retryable


def test_http_code() -> None:
    assert http_code(503) == "http_503"
    with pytest.raises(TypeError):
        http_code(True)


def test_lead_job_keys_are_the_utc_date_and_the_domain() -> None:
    late = datetime(2026, 11, 17, 23, 30, tzinfo=timezone(timedelta(hours=-5)))

    assert lead_job_keys("smith-law.co.uk", late) == ("2026-11-18", "domain:smith-law.co.uk")
    assert lead_job_keys("a.com", datetime(2026, 1, 2, tzinfo=UTC))[0] == "2026-01-02"
    with pytest.raises(TypeError):
        lead_job_keys("a.com", datetime(2026, 1, 2))  # naive


# Rendering (Story 2.6) --------------------------------------------------------------------------


def test_a_page_under_500_characters_needs_a_render() -> None:
    from nextron.domain import crawl  # noqa: PLC0415

    assert (crawl.RENDER_THRESHOLD, crawl.MAX_RENDERS, crawl.RENDER_TIMEOUT_S) == (500, 3, 20.0)
    assert crawl.RENDER_PAGES_JOB == "render_pages"
    assert crawl.needs_render("")
    assert crawl.needs_render("x" * 499)
    assert not crawl.needs_render("x" * 500)
    with pytest.raises(TypeError):
        crawl.needs_render(None)  # type: ignore[arg-type]

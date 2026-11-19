"""The rules of crawling a Lead's website (FR-10, AD-7, AD-18, NFR-6, NFR-34). Standard library
only.

**Who the crawler is.** ``USER_AGENT`` (``NextronBot/1.0 (+https://nextronai.com)``) is sent
with every request; ``ROBOTS_TOKEN`` (``NextronBot``) is the token robots.txt rules are matched
against.

**Pages.** ``PageKind`` is ``home``, ``team``, ``about``, ``careers``, ``contact``,
``services`` or ``news``, in that order (a crawl takes the links of a depth in this order).
``classify_link(path, anchor_text) -> PageKind | None`` matches whole-token keywords
(``KEYWORDS``) in a link's path, then in its anchor text:

- both are lowercased and split into tokens of letters and digits (the path is percent-decoded
  first), and a keyword of several words matches the same tokens in a row, so
  ``/practice-areas/`` and "Practice areas" are ``services``;
- the path decides when it matches; otherwise the anchor text does. Within one of them, the
  first ``PageKind`` in order wins (``/about/team`` is ``team``);
- a path whose last segment ends in a non-HTML file extension (``NON_HTML_EXTENSIONS``:
  documents, office files, images, archives, audio and video) never matches, whatever the
  anchor text;
- ``home`` is never a link's kind: it is the page a crawl starts from.

The keywords: team (team, people, our people, staff, attorneys, lawyers, solicitors), about
(about, who we are), careers (careers, career, jobs, vacancies, join us), contact (contact,
offices, locations), services (services, practice areas, expertise, what we do) and news (news,
press, blog, insights). A non-``str`` argument raises ``TypeError``.

**Outcomes.** A crawl run ends as one ``CrawlOutcome``: ``ok``, ``unreachable``,
``robots_disallowed`` or ``lead_merged``. ``OUTCOME_LABELS`` is how the Lead page (Story 2.4)
names each; ``unreachable`` is "website unreachable". ``LINK_TAGS`` (``a``, ``iframe``,
``script``) are the tags a page's links come from, and a crawl stores only pages whose media
type is in ``HTML_MEDIA_TYPES`` (``text/html``, ``application/xhtml+xml``).

**Timing.** ``crawl_interval(crawl_delay)`` is the seconds between two requests to one site:
``max(1, min(crawl_delay, 10))``, and 1 when robots.txt gives no delay (``None``), a negative
one or not a number. ``TEXT_RETENTION`` (30 days) is how long a page's text is kept,
``RUN_DEADLINE`` (10 minutes) how long one crawl may fetch pages, and ``MAX_DEPTH`` (2) how
many links away from the homepage it goes. ``request_interval(min_interval_s)`` is how a
crawler applies the interval it is given: never under 1 s (``MIN_INTERVAL_S``), and 1 s for a
NaN; a non-number raises ``TypeError``.

**Text.** ``visible_text(raw)`` normalises the text a page shows, so a quote copied from it
matches verbatim (Story 2.9): it deletes every ``Cf`` character (soft hyphen, zero-width space
and joiners, BOM, bidi controls), splits the text into lines (``str.splitlines``), collapses each
run of whitespace within a line to one space, strips each line, drops the blank ones and joins
the rest with ``\\n``. The result is capped at ``MAX_STORED_TEXT`` (100,000) characters at a line
boundary: the lines that fit are kept whole; when the first line alone is longer, it is cut at
the cap. ``text_hash(text)`` is the SHA-256 hex digest of the text's UTF-8 bytes.

**Jobs (AD-10).** ``ENRICH_LEAD_JOB`` (``enrich_lead``), ``CANONICALIZE_LEAD_JOB``
(``canonicalize_lead``), ``PURGE_PAGE_TEXT_JOB`` (``purge_page_text``) and ``RENDER_PAGES_JOB``
(``render_pages``) are the crawl's job types. A Lead's crawl jobs are keyed
``<type>:<lead ULID>:<UTC date>``: ``lead_job_keys(site, now)`` gives the discriminator
(``now``'s UTC date) and the concurrency key (``domain:<site>``), so one site is crawled by one
job at a time. Their payloads are ``{"v": 1, "lead_id": ...}`` and ``{"v": 1, "lead_id": ...,
"run_id": ...}`` (``PAYLOAD_VERSION``, ``LEAD_ID_FIELD``, ``RUN_ID_FIELD``). ``render_pages`` runs
on the bulk lane only; when its chain runs on the interactive lane (a Signal Inbox chain, Story
2.16), its payload also holds ``"chain_lane": "interactive"`` (``CHAIN_LANE_FIELD``), so the
``extract_lead`` it enqueues goes back to that lane.

**Rendering (Story 2.6).** A stored page whose visible text is shorter than
``RENDER_THRESHOLD`` (500 characters) ``needs_render``: it is probably built by JavaScript. The
``render_pages`` job (``RENDER_PAGES_JOB``) renders at most ``MAX_RENDERS`` (3) such pages of a
run, each within ``RENDER_TIMEOUT_S`` (20 s) from navigation.

**Retries.** ``is_retryable(error_code)`` is true only for ``fetch_timeout`` and the
``http_5xx`` codes (``http_500`` to ``http_599``): a flaky site, worth another attempt. Any
other code (a DNS or connect failure, a refusal, a 404) is final. ``http_code(status)`` is the
``http_<status>`` code of a response status.
"""

import hashlib
import math
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

USER_AGENT: Final = "NextronBot/1.0 (+https://nextronai.com)"
ROBOTS_TOKEN: Final = "NextronBot"

MIN_INTERVAL_S: Final = 1.0
MAX_CRAWL_DELAY_S: Final = 10.0
TEXT_RETENTION: Final = timedelta(days=30)
RUN_DEADLINE: Final = timedelta(minutes=10)
MAX_DEPTH: Final = 2
MAX_STORED_TEXT: Final = 100_000
LINK_TAGS: Final = ("a", "iframe", "script")  # the tags a page's links come from
HTML_MEDIA_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})  # pages a crawl stores

ENRICH_LEAD_JOB: Final = "enrich_lead"
CANONICALIZE_LEAD_JOB: Final = "canonicalize_lead"
PURGE_PAGE_TEXT_JOB: Final = "purge_page_text"
RENDER_PAGES_JOB: Final = "render_pages"
RENDER_THRESHOLD: Final = 500  # visible characters under which a page is rendered
MAX_RENDERS: Final = 3  # pages rendered per run
RENDER_TIMEOUT_S: Final = 20.0  # from navigation
PAYLOAD_VERSION: Final = 1
LEAD_ID_FIELD: Final = "lead_id"
RUN_ID_FIELD: Final = "run_id"
# render_pages' optional payload field: the lane the rest of the chain runs on (Story 2.16)
CHAIN_LANE_FIELD: Final = "chain_lane"
DOMAIN_KEY_PREFIX: Final = "domain:"  # nextron.domain.job.DOMAIN_PREFIX

FETCH_TIMEOUT: Final = "fetch_timeout"
HTTP_CODE_PREFIX: Final = "http_"
RETRYABLE_HTTP: Final = re.compile(r"http_5[0-9]{2}")  # matched with fullmatch
TOKEN: Final = re.compile(r"[^\W_]+")  # a run of letters and digits

NON_HTML_EXTENSIONS: Final = frozenset(
    {
        # documents and office files
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "csv",
        "txt", "epub",
        # images
        "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tif", "tiff", "heic", "avif",
        # archives
        "zip", "rar", "7z", "gz", "tgz", "tar", "bz2", "xz",
        # audio and video
        "mp3", "mp4", "m4a", "m4v", "wav", "ogg", "oga", "ogv", "webm", "avi", "mov", "wmv",
        "mkv", "flac", "aac",
    }
)  # fmt: skip


class PageKind(StrEnum):
    """What a crawled page is, in the order a crawl takes links."""

    HOME = "home"
    TEAM = "team"
    ABOUT = "about"
    CAREERS = "careers"
    CONTACT = "contact"
    SERVICES = "services"
    NEWS = "news"


KEYWORDS: Final[Mapping[PageKind, tuple[str, ...]]] = MappingProxyType(
    {
        PageKind.TEAM: (
            "team",
            "people",
            "our people",
            "staff",
            "attorneys",
            "lawyers",
            "solicitors",
        ),
        PageKind.ABOUT: ("about", "who we are"),
        PageKind.CAREERS: ("careers", "career", "jobs", "vacancies", "join us"),
        PageKind.CONTACT: ("contact", "offices", "locations"),
        PageKind.SERVICES: ("services", "practice areas", "expertise", "what we do"),
        PageKind.NEWS: ("news", "press", "blog", "insights"),
    }
)


class CrawlOutcome(StrEnum):
    """How a crawl run ended."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    ROBOTS_DISALLOWED = "robots_disallowed"
    LEAD_MERGED = "lead_merged"


OUTCOME_LABELS: Final[Mapping[CrawlOutcome, str]] = MappingProxyType(
    {
        CrawlOutcome.OK: "website crawled",
        CrawlOutcome.UNREACHABLE: "website unreachable",
        CrawlOutcome.ROBOTS_DISALLOWED: (
            "website not crawled: its robots.txt disallows it or could not be read"
        ),
        CrawlOutcome.LEAD_MERGED: "Lead merged during the crawl",
    }
)


# Links ----------------------------------------------------------------------------------------


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(TOKEN.findall(text.lower()))


def _holds(tokens: tuple[str, ...], keyword: str) -> bool:
    """Whether ``tokens`` hold every word of ``keyword`` in a row."""
    words = tuple(keyword.split())
    width = len(words)
    return any(tokens[i : i + width] == words for i in range(len(tokens) - width + 1))


def _kind(tokens: tuple[str, ...]) -> PageKind | None:
    for kind, keywords in KEYWORDS.items():
        if any(_holds(tokens, keyword) for keyword in keywords):
            return kind
    return None


def _is_file(path: str) -> bool:
    """Whether ``path``'s last segment ends in a non-HTML file extension."""
    last = path.rstrip("/").rsplit("/", 1)[-1]
    last = last.split(";", 1)[0]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[1].lower() in NON_HTML_EXTENSIONS


def classify_link(path: str, anchor_text: str) -> PageKind | None:
    """The kind of page a link leads to, or ``None``; see the module docstring."""
    if not isinstance(path, str) or not isinstance(anchor_text, str):
        raise TypeError("classify_link takes a path and an anchor text as str")
    decoded = urllib.parse.unquote(path)
    if _is_file(decoded):
        return None
    found = _kind(_tokens(decoded))
    return found if found is not None else _kind(_tokens(anchor_text))


# Timing ---------------------------------------------------------------------------------------


def crawl_interval(crawl_delay: float | None) -> float:
    """Seconds between two requests to one site: robots.txt's delay, within 1 to 10."""
    if crawl_delay is None or isinstance(crawl_delay, bool):
        return MIN_INTERVAL_S
    if not isinstance(crawl_delay, int | float):
        raise TypeError("crawl_interval takes a number of seconds or None")
    if math.isnan(crawl_delay):
        return MIN_INTERVAL_S
    return max(MIN_INTERVAL_S, min(float(crawl_delay), MAX_CRAWL_DELAY_S))


def request_interval(min_interval_s: float) -> float:
    """The least seconds between two requests to one site, as a crawler applies a caller's
    ``min_interval_s``: never under ``MIN_INTERVAL_S``; a NaN is ``MIN_INTERVAL_S``."""
    if isinstance(min_interval_s, bool) or not isinstance(min_interval_s, int | float):
        raise TypeError("min_interval_s must be a number of seconds")
    if math.isnan(min_interval_s):
        return MIN_INTERVAL_S
    return max(MIN_INTERVAL_S, float(min_interval_s))


# Text -----------------------------------------------------------------------------------------


def _cap(lines: list[str]) -> str:
    kept: list[str] = []
    size = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if size + added > MAX_STORED_TEXT:
            break
        kept.append(line)
        size += added
    if not kept and lines:
        return lines[0][:MAX_STORED_TEXT]
    return "\n".join(kept)


def visible_text(raw: str) -> str:
    """``raw`` as a page shows it, normalised and capped; see the module docstring."""
    if not isinstance(raw, str):
        raise TypeError(f"visible_text takes a str, not {type(raw).__name__}")
    text = "".join(ch for ch in raw if unicodedata.category(ch) != "Cf")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return _cap([line for line in lines if line])


def text_hash(text: str) -> str:
    """The SHA-256 hex digest of ``text``'s UTF-8 bytes."""
    if not isinstance(text, str):
        raise TypeError(f"text_hash takes a str, not {type(text).__name__}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def needs_render(text: str) -> bool:
    """Whether a page whose visible text is ``text`` is rendered (under ``RENDER_THRESHOLD``)."""
    if not isinstance(text, str):
        raise TypeError(f"needs_render takes a str, not {type(text).__name__}")
    return len(text) < RENDER_THRESHOLD


# Jobs -----------------------------------------------------------------------------------------


def lead_job_keys(site: str, now: datetime) -> tuple[str, str]:
    """The discriminator (``now``'s UTC date) and concurrency key (``domain:<site>``) of a Lead's
    crawl jobs."""
    if not isinstance(site, str) or not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("lead_job_keys takes a site as str and an aware datetime")
    return now.astimezone(UTC).date().isoformat(), f"{DOMAIN_KEY_PREFIX}{site}"


# Retries --------------------------------------------------------------------------------------


def http_code(status: int) -> str:
    """The ``http_<status>`` error code of a response status."""
    if isinstance(status, bool) or not isinstance(status, int):
        raise TypeError("http_code takes an int status")
    return f"{HTTP_CODE_PREFIX}{status}"


def is_retryable(error_code: str | None) -> bool:
    """Whether a crawl that failed with ``error_code`` may succeed on another attempt."""
    if error_code is None:
        return False
    if not isinstance(error_code, str):
        raise TypeError("is_retryable takes an error code as str, or None")
    return error_code == FETCH_TIMEOUT or RETRYABLE_HTTP.fullmatch(error_code) is not None

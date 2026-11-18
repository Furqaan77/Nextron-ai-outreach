"""The rules of job postings (FR-7, FR-11, FR-49, AD-7, AD-9, INT-10; Story 2.11). Standard
library and ``nextron.domain`` only.

A Lead's job postings come from two places: its own careers pages (the ``job_postings`` Claude's
``extract_signals`` answer proposes for them, Story 2.9's call) and the public job boards its
website links to (Greenhouse, Lever and Ashby, through ``nextron.ports.job_board``). A posting
that matches a hiring Signal type's terms (``nextron.domain.signals.match_hiring``) becomes one
dated Event; a posting no longer listed closes, and its Event stops being live. Nothing here
reads or writes the database.

**Constants.** ``CHECK_JOB_BOARDS_JOB`` (``check_job_boards``) and ``RECHECK_EVENT_JOB``
(``recheck_event``) are the job types; ``SIGNAL_ID_FIELD`` (``signal_id``) is the payload field
of a ``recheck_event`` job. ``MAX_TITLE`` (300) is the longest title stored and ``MAX_EXCERPT``
(300) the longest description excerpt.

**Enums.** ``PostingSource`` (``careers_page``, ``greenhouse``, ``lever``, ``ashby``; the last
three are ``ATS_SOURCES``, in that order) and ``PostingStatus`` (``live``, ``closed``).

**Boards.** ``BOARD_HOSTS`` (read-only) maps each board host the engine can read to its source:
``boards.greenhouse.io`` and ``job-boards.greenhouse.io`` to ``greenhouse``, ``jobs.lever.co`` to
``lever`` and ``jobs.ashbyhq.com`` to ``ashby``. ``Board(source, token)`` is one board (ordered by
source, then token).

- ``board_from_link(url, ats_hosts) -> Board | None``: the board an ATS link of the Lead's
  website names. The URL must be ``http`` or ``https``, and its host (lowercased) must be in
  ``ats_hosts`` **and** in ``BOARD_HOSTS`` (a configured host with no endpoint in code is never
  read). The token is, for Greenhouse, the single ``for`` query value on ``/embed/job_board``,
  ``/embed/job_board/js`` or ``/embed/job_app``, else the first path segment, which must not be
  ``embed``; for Lever and Ashby, the first path segment. A token must match
  ``[A-Za-z0-9][A-Za-z0-9_-]{0,99}`` (``TOKEN``); anything else is ``None``.
- ``source_enabled(source, ats_hosts) -> bool``: whether some host of ``ats_hosts`` is a
  ``BOARD_HOSTS`` host of that source. ``careers_page`` has no host: ``False``.

**Text.** ``posting_title(title) -> str | None``: the title normalised (``quotes.normalise``),
or ``None`` when that is empty or longer than ``MAX_TITLE``. ``excerpt(description, term=None)
-> str | None``: the normalised description when it has at most ``MAX_EXCERPT`` characters
(``None`` for ``None`` or an empty one). A longer one gives a window of at most ``MAX_EXCERPT``
characters cut on whitespace (a word the window's edge would cut is left out; a window with no
whitespace to cut on is cut at its edge): without a ``term``, its start; with one, a window
around the term's first whole-phrase
occurrence (case-insensitive, no letter or digit just before or after it, as
``signals.has_phrase`` reads it), which it always holds. A term that isn't found gives the
start.

**Careers postings.** ``validate_postings(parsed, pages, careers_ids, now) ->
PostingValidation(postings, discards, dropped_dates)`` reads the ``job_postings`` of an
``extract_signals`` answer (``title``, ``url``, ``posted_date``, ``block_id``, ``quote``).
``pages`` maps each page id sent to its ``extraction.SentPage``; ``careers_ids`` are the ids of
the pages sent whose kind is ``careers``. Each proposal is checked in this order, and the first
failure discards it (a ``PostingDiscard(status, cause, page_id)`` with status
``job_posting_discarded``; ``page_id`` is the cited page when it was sent):

1. its ``block_id`` is a page sent (``unknown_block``, ``page_id`` ``None``);
2. that page is a careers page (``not_careers_page``);
3. its title, normalised, isn't empty (``title_empty``), has at most ``MAX_TITLE`` characters
   (``title_too_long``) and passes ``quote_in`` against the page's full stored text
   (``title_not_in_page``).

A kept proposal that repeats an earlier kept one (the same page and normalised title) is
dropped with no record. The model's ``url`` can't be verified and is ignored: the posting's URL
is the page's. Its quote, normalised, is the posting's description when it has 1 to 300
characters and passes ``quote_in`` against the page; otherwise the description is ``None``. A
``posted_date`` counts only when that description holds it (``extraction.stated_date``) and it
isn't after ``now``'s UTC date; otherwise it is dropped (``dropped_dates``, status
``job_posting_date_discarded``, cause ``date_not_in_quote`` or ``date_in_future``) and the
posting kept undated. ``CareersPosting(page_id, url, title, description, posted_date)`` is a kept
one (its title and description never show in a ``repr``). An answer without the schema's shape
raises ``ValueError``.
"""

import re
import urllib.parse
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from nextron.domain.extraction import DiscardCause, SentPage, stated_date
from nextron.domain.quotes import normalise, quote_in

CHECK_JOB_BOARDS_JOB: Final = "check_job_boards"
RECHECK_EVENT_JOB: Final = "recheck_event"
SIGNAL_ID_FIELD: Final = "signal_id"
MAX_TITLE: Final = 300
MAX_EXCERPT: Final = 300
TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")  # matched with fullmatch
GREENHOUSE_EMBEDS: Final = frozenset({"/embed/job_board", "/embed/job_board/js", "/embed/job_app"})
GREENHOUSE_EMBED_SEGMENT: Final = "embed"
GREENHOUSE_EMBED_FIELD: Final = "for"
WEB_SCHEMES: Final = frozenset({"http", "https"})


class PostingSource(StrEnum):
    """Where a job posting was found."""

    CAREERS_PAGE = "careers_page"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


ATS_SOURCES: Final = (PostingSource.GREENHOUSE, PostingSource.LEVER, PostingSource.ASHBY)


class PostingStatus(StrEnum):
    """Whether a posting is still listed."""

    LIVE = "live"
    CLOSED = "closed"


BOARD_HOSTS: Final[Mapping[str, PostingSource]] = MappingProxyType(
    {
        "boards.greenhouse.io": PostingSource.GREENHOUSE,
        "job-boards.greenhouse.io": PostingSource.GREENHOUSE,
        "jobs.lever.co": PostingSource.LEVER,
        "jobs.ashbyhq.com": PostingSource.ASHBY,
    }
)


class PostingDiscarded(StrEnum):
    """What a careers posting's refusal ``events`` row says was discarded (its ``to_status``)."""

    POSTING = "job_posting_discarded"
    DATE = "job_posting_date_discarded"


class PostingDiscardCause(StrEnum):
    """Why a careers posting was discarded (the ``events`` row's ``cause``)."""

    UNKNOWN_BLOCK = "unknown_block"
    NOT_CAREERS_PAGE = "not_careers_page"
    TITLE_EMPTY = "title_empty"
    TITLE_TOO_LONG = "title_too_long"
    TITLE_NOT_IN_PAGE = "title_not_in_page"


# Boards ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Board:
    """One job board: its source and its token."""

    source: PostingSource
    token: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, PostingSource) or self.source not in ATS_SOURCES:
            raise TypeError("a Board's source must be an ATS PostingSource")
        if not isinstance(self.token, str) or TOKEN.fullmatch(self.token) is None:
            raise ValueError("a Board's token must match TOKEN")


def _hosts(ats_hosts: Collection[str]) -> frozenset[str]:
    if isinstance(ats_hosts, str):
        raise TypeError("ats_hosts must be a collection of host names, not a str")
    return frozenset(host.strip().lower() for host in ats_hosts if isinstance(host, str))


def _token(value: str | None) -> str | None:
    return value if value is not None and TOKEN.fullmatch(value) is not None else None


def board_from_link(url: str, ats_hosts: Collection[str]) -> Board | None:
    """The board an ATS link names, or ``None``; see the module docstring."""
    if not isinstance(url, str):
        raise TypeError("url must be a str")
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in WEB_SCHEMES or host is None:
        return None
    if host not in _hosts(ats_hosts) or host not in BOARD_HOSTS:
        return None
    source = BOARD_HOSTS[host]
    path = parts.path or "/"
    segments = [segment for segment in path.split("/") if segment]
    first = segments[0] if segments else None
    if source is PostingSource.GREENHOUSE:
        if path.rstrip("/") in GREENHOUSE_EMBEDS:
            values = urllib.parse.parse_qs(parts.query, keep_blank_values=True).get(
                GREENHOUSE_EMBED_FIELD, []
            )
            token = _token(values[0]) if len(values) == 1 else None
        elif first == GREENHOUSE_EMBED_SEGMENT:
            token = None
        else:
            token = _token(first)
    else:
        token = _token(first)
    return None if token is None else Board(source, token)


def source_enabled(source: PostingSource, ats_hosts: Collection[str]) -> bool:
    """Whether some host of ``ats_hosts`` is a board host of ``source``."""
    if not isinstance(source, PostingSource):
        raise TypeError("source must be a PostingSource")
    hosts = _hosts(ats_hosts)
    return any(BOARD_HOSTS[host] is source for host in hosts if host in BOARD_HOSTS)


# Text ------------------------------------------------------------------------------------------


def posting_title(title: str) -> str | None:
    """The title as stored: normalised, 1 to ``MAX_TITLE`` characters, else ``None``."""
    if not isinstance(title, str):
        raise TypeError("title must be a str")
    normal = normalise(title)
    return normal if 1 <= len(normal) <= MAX_TITLE else None


def _phrase_at(text: str, phrase: str) -> tuple[int, int] | None:
    """The span of ``phrase``'s first whole-phrase, case-insensitive occurrence in ``text``."""
    folded = normalise(phrase).casefold()
    if not folded:
        return None
    for width in sorted({len(phrase), len(normalise(phrase)), len(folded)}):
        for start in range(len(text) - width + 1):
            if text[start : start + width].casefold() != folded:
                continue
            end = start + width
            before = text[start - 1] if start > 0 else " "
            after = text[end] if end < len(text) else " "
            if not before.isalnum() and not after.isalnum():
                return start, end
    return None


def _window(text: str, start: int, end: int) -> str:
    """At most ``MAX_EXCERPT`` characters of ``text`` holding ``[start, end)``, cut on
    whitespace where it can be."""
    spare = MAX_EXCERPT - (end - start)
    if spare <= 0:
        return text[start : start + MAX_EXCERPT].strip()
    left = max(0, start - spare // 2)
    right = min(len(text), left + MAX_EXCERPT)
    left = max(0, right - MAX_EXCERPT)
    if left > 0 and text[left - 1] != " ":  # mid-word: start after the next space
        space = text.find(" ", left, start)
        left = space + 1 if space != -1 else left
    if right < len(text) and text[right] != " ":  # mid-word: end at the last space
        space = text.rfind(" ", end, right)
        right = space if space != -1 else right
    return text[left:right].strip()


def excerpt(description: str | None, term: str | None = None) -> str | None:
    """The description's excerpt; see the module docstring."""
    if description is None:
        return None
    if not isinstance(description, str):
        raise TypeError("description must be a str or None")
    if term is not None and not isinstance(term, str):
        raise TypeError("term must be a str or None")
    text = normalise(description)
    if not text:
        return None
    if len(text) <= MAX_EXCERPT:
        return text
    span = None if term is None else _phrase_at(text, term)
    if span is None:
        return _window(text, 0, 0) or text[:MAX_EXCERPT]
    return _window(text, *span)


# Careers postings ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CareersPosting:
    """One kept careers posting; its title and description never show in a ``repr``."""

    page_id: str
    url: str
    title: str = field(repr=False)
    description: str | None = field(repr=False)
    posted_date: date | None


@dataclass(frozen=True, slots=True)
class PostingDiscard:
    """One refusal: what was discarded, why, and the cited page when it was sent."""

    status: PostingDiscarded
    cause: PostingDiscardCause | DiscardCause
    page_id: str | None


@dataclass(frozen=True, slots=True)
class PostingValidation:
    """What ``validate_postings`` kept and refused."""

    postings: tuple[CareersPosting, ...]
    discards: tuple[PostingDiscard, ...]
    dropped_dates: tuple[PostingDiscard, ...]


def _text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str):
        raise ValueError(f"the extract_signals answer's job_postings {name} must be a str")
    return value


def _posted(item: Mapping[str, Any]) -> date | None:
    value = item.get("posted_date")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("the extract_signals answer's posted_date must be a date or null")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("the extract_signals answer's posted_date must be a date") from None


def _title_cause(title: str, page: SentPage) -> PostingDiscardCause | None:
    if not title:
        return PostingDiscardCause.TITLE_EMPTY
    if len(title) > MAX_TITLE:
        return PostingDiscardCause.TITLE_TOO_LONG
    if not quote_in(title, page.text):
        return PostingDiscardCause.TITLE_NOT_IN_PAGE
    return None


def validate_postings(
    parsed: Mapping[str, Any],
    pages: Mapping[str, SentPage],
    careers_ids: Collection[str],
    now: datetime,
) -> PostingValidation:
    """The careers postings kept, refused and with their dates dropped; see the module
    docstring."""
    if not isinstance(parsed, Mapping):
        raise ValueError("the extract_signals answer must be an object")
    if not all(isinstance(page, SentPage) for page in pages.values()):
        raise TypeError("pages must map page ids to SentPage")
    if isinstance(careers_ids, str):
        raise TypeError("careers_ids must be a collection of page ids, not a str")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("now must be an aware datetime")
    items = parsed.get("job_postings")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise ValueError("the extract_signals answer's job_postings must be a list of objects")
    today = now.astimezone(UTC).date()
    careers = frozenset(careers_ids)
    kept: list[CareersPosting] = []
    seen: set[tuple[str, str]] = set()
    discards: list[PostingDiscard] = []
    dropped: list[PostingDiscard] = []
    for item in items:
        block_id = _text(item, "block_id")
        raw_title = _text(item, "title")
        quote = normalise(_text(item, "quote"))
        if item.get("url") is not None and not isinstance(item.get("url"), str):
            raise ValueError("the extract_signals answer's job_postings url must be a str")
        proposed_date = _posted(item)
        page = pages.get(block_id)
        if page is None:
            discards.append(
                PostingDiscard(PostingDiscarded.POSTING, PostingDiscardCause.UNKNOWN_BLOCK, None)
            )
            continue
        if block_id not in careers:
            discards.append(
                PostingDiscard(
                    PostingDiscarded.POSTING, PostingDiscardCause.NOT_CAREERS_PAGE, block_id
                )
            )
            continue
        title = normalise(raw_title)
        cause = _title_cause(title, page)
        if cause is not None:
            discards.append(PostingDiscard(PostingDiscarded.POSTING, cause, block_id))
            continue
        if (block_id, title) in seen:  # a repeat is dropped without a record
            continue
        seen.add((block_id, title))
        in_page = 1 <= len(quote) <= MAX_EXCERPT and quote_in(quote, page.text)
        description = quote if in_page else None
        posted_date = None
        if proposed_date is not None:
            date_cause = None
            if description is None or not stated_date(description, proposed_date):
                date_cause = DiscardCause.DATE_NOT_IN_QUOTE
            elif proposed_date > today:
                date_cause = DiscardCause.DATE_IN_FUTURE
            if date_cause is None:
                posted_date = proposed_date
            else:
                dropped.append(PostingDiscard(PostingDiscarded.DATE, date_cause, block_id))
        kept.append(CareersPosting(block_id, page.url, title, description, posted_date))
    return PostingValidation(tuple(kept), tuple(discards), tuple(dropped))

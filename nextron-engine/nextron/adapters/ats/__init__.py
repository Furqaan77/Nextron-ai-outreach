"""The public job boards (FR-7, FR-11, FR-49, AD-9, INT-10, NFR-6; Story 2.11): ``JobBoardPort``
over the Greenhouse, Lever and Ashby public JSON endpoints, every request made by the crawler's
egress-filtered client.

Import rule: ``nextron.ports``, ``nextron.domain``, ``nextron.adapters.crawler`` and the standard
library. Nothing here logs.

**``AtsBoards(*, meter, fetcher=None)``** implements ``JobBoardPort``. ``fetcher``
(``fetch_resource``, as ``nextron.adapters.crawler.HttpCrawler`` has it: the ``JsonFetcher``
protocol) does every request, so each hop is checked against the egress rules and the FR-49
denylist, resolved, pinned, redirected, capped and metered there. Without one,
``default_fetcher(meter)`` (``HttpCrawler(meter=meter)``, the live constructor) is looked up on
the first call, so a test can refuse it.

**The endpoints are held in code** (``ENDPOINTS``; INT-10: an endpoint is checked before it is
used), each with the registrable domain its requests and redirects are pinned to:

- Greenhouse: ``https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true``
  (``greenhouse.io``);
- Lever: ``https://api.lever.co/v0/postings/{token}?mode=json`` (``lever.co``);
- Ashby: ``https://api.ashbyhq.com/posting-api/job-board/{token}`` (``ashbyhq.com``).

A board of any other source, or whose token isn't ``nextron.domain.postings.TOKEN``, raises
``board_failed`` before any request. No setting or parameter changes the table.

**``fetch_json(url, *, site) -> object``** is one request: ``fetcher.fetch_resource(url,
site=site, accepts=<application/json or a +json type>, accept_header="application/json",
operation="board", deadline=None, min_interval_s=MIN_INTERVAL_S, provider="ats")``, so each
request is one ``ApiCall`` ``("ats", "board")``, paced at 1 s per domain with no deadline beyond
the fetch's own. A 2xx body is decoded (its charset, else UTF-8; a byte-order mark dropped) and
read as JSON (``board_invalid`` when it isn't). A 404 or 410 is ``board_not_found``; a 429 or a
5xx the retryable ``board_unavailable``; any other status ``board_failed``. The fetcher's own
``AdapterError`` (a ``FETCH_*`` code) passes through unchanged.

**``postings(board) -> BoardListing``** calls ``fetch_json`` on the board's endpoint and maps the
answer (anything but the shape below is ``board_invalid``):

- Greenhouse: an object whose ``jobs`` is a list; each job's ``title``, ``absolute_url``,
  ``first_published`` and ``content``, which is HTML-unescaped (``html.unescape``) and reduced to
  its visible text (``nextron.adapters.crawler.parsing.parse_html``);
- Lever: a list; each posting's ``text`` (the title), ``hostedUrl``, ``createdAt``
  (milliseconds since the epoch) and ``descriptionPlain`` and ``additionalPlain`` (joined by a
  line break);
- Ashby: an object whose ``jobs`` is a list; each job's ``title``, ``jobUrl``, ``publishedAt``
  and ``descriptionPlain``; a job with ``isListed`` ``false`` is skipped.

An entry that isn't an object, has no title with text, or no absolute ``http(s)`` URL with a
host of at most 2,048 characters and no whitespace, is skipped. A date is read as a UTC date
(an ISO 8601 date or time, or Lever's milliseconds); one that can't be read, or that is after
today's UTC date, is ``None``. A description is cut to ``MAX_DESCRIPTION`` (20,000) characters;
an empty one is ``None``.

Every error is an ``AdapterError`` with this module's own message, raised ``from None``: none
carries a URL, a token or any posting text.
"""

import html
import json
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, Final, Protocol

from nextron.adapters.crawler import HttpCrawler, Resource
from nextron.adapters.crawler.parsing import decode, parse_html
from nextron.domain.crawl import MIN_INTERVAL_S
from nextron.domain.fetch_policy import MAX_URL_LENGTH
from nextron.domain.postings import TOKEN, Board, PostingSource
from nextron.ports.errors import AdapterError
from nextron.ports.job_board import (
    BOARD_FAILED,
    BOARD_INVALID,
    BOARD_NOT_FOUND,
    BOARD_UNAVAILABLE,
    BoardListing,
    BoardPosting,
)
from nextron.ports.usage_meter import UsageMeterPort

PROVIDER: Final = "ats"  # the usage meter's provider and operation for every request
OPERATION: Final = "board"
ACCEPT_HEADER: Final = "application/json"
JSON_MEDIA_TYPE: Final = "application/json"
JSON_SUFFIX: Final = "+json"
MAX_DESCRIPTION: Final = 20_000
NOT_FOUND_STATUSES: Final = frozenset({404, 410})
TOO_MANY_REQUESTS: Final = 429
WEB_SCHEMES: Final = frozenset({"http", "https"})
BYTE_ORDER_MARK: Final = "﻿"
MILLISECONDS: Final = 1000
INVALID: Final = "the job board's answer isn't its JSON shape"

# The provider's endpoint for a token, and the registrable domain its requests are pinned to.
ENDPOINTS: Final[Mapping[PostingSource, tuple[str, str]]] = MappingProxyType(
    {
        PostingSource.GREENHOUSE: (
            "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
            "greenhouse.io",
        ),
        PostingSource.LEVER: ("https://api.lever.co/v0/postings/{token}?mode=json", "lever.co"),
        PostingSource.ASHBY: (
            "https://api.ashbyhq.com/posting-api/job-board/{token}",
            "ashbyhq.com",
        ),
    }
)


class JsonFetcher(Protocol):
    """What the boards need of the crawler: one filtered fetch of a resource."""

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
        provider: str,
    ) -> Resource: ...


def default_fetcher(meter: UsageMeterPort) -> JsonFetcher:
    """The live fetcher: an ``HttpCrawler`` on ``meter``."""
    return HttpCrawler(meter=meter)


def _is_json(media_type: str) -> bool:
    return media_type == JSON_MEDIA_TYPE or (
        "/" in media_type and media_type.endswith(JSON_SUFFIX)
    )


def _text(value: object) -> str | None:
    """``value`` when it is a ``str`` with any non-whitespace text, else ``None``."""
    return value if isinstance(value, str) and value.strip() else None


def _url(value: object) -> str | None:
    """``value`` when it is an absolute ``http(s)`` URL with a host, at most 2,048 characters and
    with no whitespace, else ``None``."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        return None
    if any(character.isspace() for character in value):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    return value if parts.scheme.lower() in WEB_SCHEMES and host else None


def _today() -> date:
    return datetime.now(UTC).date()


def _not_future(day: date) -> date | None:
    return None if day > _today() else day


def _iso_date(value: object) -> date | None:
    """An ISO 8601 date or time as a UTC date, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
        if moment.utcoffset() is not None:
            moment = moment.astimezone(UTC)  # 0001-01-01 with an offset east of UTC overflows
        day = moment.date()
    except (OverflowError, ValueError):
        return None
    return _not_future(day)


def _milliseconds_date(value: object) -> date | None:
    """Milliseconds since the epoch as a UTC date, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return _not_future(datetime.fromtimestamp(value / MILLISECONDS, UTC).date())
    except (OverflowError, OSError, ValueError):
        return None


def _description(text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    return text[:MAX_DESCRIPTION]


def _posting(
    title: object, url: object, posted: date | None, description: str | None
) -> BoardPosting | None:
    found_title, found_url = _text(title), _url(url)
    if found_title is None or found_url is None:
        return None
    return BoardPosting(found_title, found_url, posted, _description(description))


def _jobs(answer: object) -> list[Any]:
    if not isinstance(answer, Mapping):
        raise AdapterError(False, BOARD_INVALID, INVALID)
    jobs = answer.get("jobs")
    if not isinstance(jobs, list):
        raise AdapterError(False, BOARD_INVALID, INVALID)
    return jobs


def _greenhouse(answer: object) -> list[BoardPosting | None]:
    found: list[BoardPosting | None] = []
    for job in _jobs(answer):
        if not isinstance(job, Mapping):
            found.append(None)
            continue
        content = job.get("content")
        url = _url(job.get("absolute_url"))
        text = None
        if isinstance(content, str) and url is not None:
            text = parse_html(html.unescape(content), url).text
        found.append(
            _posting(job.get("title"), url, _iso_date(job.get("first_published")), text)
        )
    return found


def _lever(answer: object) -> list[BoardPosting | None]:
    if not isinstance(answer, list):
        raise AdapterError(False, BOARD_INVALID, INVALID)
    found: list[BoardPosting | None] = []
    for posting in answer:
        if not isinstance(posting, Mapping):
            found.append(None)
            continue
        parts = [
            part
            for part in (posting.get("descriptionPlain"), posting.get("additionalPlain"))
            if isinstance(part, str) and part.strip()
        ]
        found.append(
            _posting(
                posting.get("text"),
                posting.get("hostedUrl"),
                _milliseconds_date(posting.get("createdAt")),
                "\n".join(parts) if parts else None,
            )
        )
    return found


def _ashby(answer: object) -> list[BoardPosting | None]:
    found: list[BoardPosting | None] = []
    for job in _jobs(answer):
        if not isinstance(job, Mapping) or job.get("isListed") is False:
            found.append(None)
            continue
        description = job.get("descriptionPlain")
        found.append(
            _posting(
                job.get("title"),
                job.get("jobUrl"),
                _iso_date(job.get("publishedAt")),
                description if isinstance(description, str) else None,
            )
        )
    return found


READERS: Final[Mapping[PostingSource, Callable[[object], list[BoardPosting | None]]]] = (
    MappingProxyType(
        {
            PostingSource.GREENHOUSE: _greenhouse,
            PostingSource.LEVER: _lever,
            PostingSource.ASHBY: _ashby,
        }
    )
)


class AtsBoards:
    """``JobBoardPort`` over the providers' public endpoints; see the module docstring."""

    def __init__(self, *, meter: UsageMeterPort, fetcher: JsonFetcher | None = None) -> None:
        self._meter = meter
        self._fetcher = fetcher

    def _fetch(self) -> JsonFetcher:
        if self._fetcher is None:
            self._fetcher = default_fetcher(self._meter)  # looked up now, so tests can refuse it
        return self._fetcher

    def fetch_json(self, url: str, *, site: str | None) -> object:
        """One request's JSON answer; see the module docstring."""
        resource = self._fetch().fetch_resource(
            url,
            site=site,
            accepts=_is_json,
            accept_header=ACCEPT_HEADER,
            operation=OPERATION,
            deadline=None,
            min_interval_s=MIN_INTERVAL_S,
            provider=PROVIDER,
        )
        status = resource.status
        if status in NOT_FOUND_STATUSES:
            raise AdapterError(False, BOARD_NOT_FOUND, "the job board no longer exists")
        if status == TOO_MANY_REQUESTS or 500 <= status < 600:
            raise AdapterError(True, BOARD_UNAVAILABLE, "the job board is unavailable for now")
        if not 200 <= status < 300:
            raise AdapterError(False, BOARD_FAILED, "the job board refused the request")
        text = decode(resource.body, resource.charset).removeprefix(BYTE_ORDER_MARK)
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            raise AdapterError(False, BOARD_INVALID, INVALID) from None

    def postings(self, board: Board) -> BoardListing:
        """The postings ``board`` lists; see the module docstring."""
        if not isinstance(board, Board) or board.source not in ENDPOINTS:
            raise AdapterError(False, BOARD_FAILED, "the job board has no endpoint")
        if TOKEN.fullmatch(board.token) is None:
            raise AdapterError(False, BOARD_FAILED, "the job board's token isn't valid")
        template, site = ENDPOINTS[board.source]
        answer = self.fetch_json(template.format(token=board.token), site=site)
        found = READERS[board.source](answer)
        return BoardListing(tuple(posting for posting in found if posting is not None))

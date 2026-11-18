"""The ``JobBoardPort`` (FR-7, FR-11, FR-49, AD-9, INT-10, NFR-6; Story 2.11): the postings a
public job board lists, read inside the egress filter.

``nextron.adapters.ats.AtsBoards`` implements it; tests use
``tests.fakes.job_board.ScriptedJobBoards``.

**``postings(board) -> BoardListing(postings)``** reads one board
(``nextron.domain.postings.Board``: Greenhouse, Lever or Ashby, and its token) at the provider's
fixed public endpoint, and returns every posting it lists, in the provider's order, each a
``BoardPosting(title, url, posted_date, description)``: the title as listed (never empty), the
posting's absolute ``http(s)`` URL (at most 2,048 characters), the UTC date it was published
(``None`` when the provider gives none, it can't be read or it is in the future) and its
description as plain text (``None`` when it has none; at most 20,000 characters). Its text (the
title, the URL and the description) never shows in a ``repr``.

**Failures.** Every failure raises ``AdapterError``:

- ``board_not_found``: the board answered 404 or 410 (it no longer exists);
- ``board_unavailable`` (retryable): it answered 429 or a 5xx;
- ``board_failed``: any other status, or a board the adapter has no endpoint for;
- ``board_invalid``: the answer isn't the provider's JSON shape;
- a ``FETCH_*`` code of ``nextron.ports.crawler`` from the fetch itself (a refusal, a DNS or
  connect failure, the retryable ``fetch_timeout``, a body too large, a media type other than
  JSON).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Protocol, runtime_checkable

from nextron.domain.postings import Board

BOARD_NOT_FOUND: Final = "board_not_found"
BOARD_UNAVAILABLE: Final = "board_unavailable"
BOARD_FAILED: Final = "board_failed"
BOARD_INVALID: Final = "board_invalid"
BOARD_CODES: Final = (BOARD_NOT_FOUND, BOARD_UNAVAILABLE, BOARD_FAILED, BOARD_INVALID)


@dataclass(frozen=True, slots=True)
class BoardPosting:
    """One posting a board lists; its title, URL and description never show in a ``repr``."""

    title: str = field(repr=False)
    url: str = field(repr=False)
    posted_date: date | None = None
    description: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not isinstance(self.url, str):
            raise TypeError("a BoardPosting's title and url must be str")
        if self.posted_date is not None and (
            isinstance(self.posted_date, datetime) or not isinstance(self.posted_date, date)
        ):
            raise TypeError("a BoardPosting's posted_date must be a date or None")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("a BoardPosting's description must be a str or None")


@dataclass(frozen=True, slots=True)
class BoardListing:
    """Every posting one board lists."""

    postings: tuple[BoardPosting, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.postings, tuple) or not all(
            isinstance(posting, BoardPosting) for posting in self.postings
        ):
            raise TypeError("a BoardListing's postings must be a tuple of BoardPosting")


@runtime_checkable
class JobBoardPort(Protocol):
    """Reads a public job board's postings inside the egress filter."""

    def postings(self, board: Board) -> BoardListing:
        """The postings ``board`` lists; see the module docstring."""
        ...

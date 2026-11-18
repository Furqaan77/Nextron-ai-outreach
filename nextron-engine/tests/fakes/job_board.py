"""``ScriptedJobBoards``: the scripted fake of ``nextron.ports.job_board.JobBoardPort``.

``ScriptedJobBoards(boards=...)`` answers each board by its exact ``Board``: ``boards`` maps a
``nextron.domain.postings.Board`` to a ``BoardListing`` (returned), an ``AdapterError`` (raised)
or a no-argument callable returning or raising one of those (so a test can move its clock or
change the answer between calls). The script is kept, so a board answers the same every time;
a board with no answer raises ``AssertionError``, so a test can't make a call it didn't plan.

``calls`` records every board asked, in order. It fetches and meters nothing.

``listing(*postings)`` and ``posting(title, url, *, posted=None, description=None)`` build
answers.
"""

from collections.abc import Callable, Mapping
from datetime import date

from nextron.domain.postings import Board
from nextron.ports.errors import AdapterError
from nextron.ports.job_board import BoardListing, BoardPosting

type BoardAnswer = BoardListing | AdapterError | Callable[[], BoardListing]


def posting(
    title: str, url: str, *, posted: date | None = None, description: str | None = None
) -> BoardPosting:
    """One posting a board lists."""
    return BoardPosting(title=title, url=url, posted_date=posted, description=description)


def listing(*postings: BoardPosting) -> BoardListing:
    """A board's answer listing ``postings``."""
    return BoardListing(tuple(postings))


class ScriptedJobBoards:
    def __init__(self, *, boards: Mapping[Board, BoardAnswer] | None = None) -> None:
        self.boards: dict[Board, BoardAnswer] = dict(boards or {})
        self.calls: list[Board] = []

    def postings(self, board: Board) -> BoardListing:
        if not isinstance(board, Board):
            raise TypeError("ScriptedJobBoards.postings takes a Board")
        self.calls.append(board)
        if board not in self.boards:
            raise AssertionError(f"ScriptedJobBoards: no answer is scripted for {board}")
        answer = self.boards[board]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        if not isinstance(answer, BoardListing):
            raise AssertionError("ScriptedJobBoards: an answer must be a BoardListing")
        return answer

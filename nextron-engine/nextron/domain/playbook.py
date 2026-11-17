"""The Sales Playbook as a stored, hashed snapshot (FR-71, AD-8, AD-12, NFR-25; Story 4.4).
Standard library only.

**Reading the file.** ``read_playbook(data: bytes) -> PlaybookSnapshot`` turns the bytes of
``docs/playbook/sales-playbook.md`` into a snapshot, or raises ``InvalidPlaybook`` carrying a
``PlaybookProblem``. In order:

1. more than 4 x ``MAX_PLAYBOOK_CHARS`` bytes is ``too_large``;
2. the bytes are decoded as strict UTF-8, else ``not_utf8``; one leading U+FEFF is dropped and
   the newlines are made universal (``\\r\\n`` and a lone ``\\r`` become ``\\n``), so a CRLF
   checkout hashes the same as the LF text;
3. more than ``MAX_PLAYBOOK_CHARS`` (100,000) characters is ``too_large``;
4. the front matter runs from a first line ``---`` to the next line ``---`` (trailing spaces
   and tabs are ignored on both). No front matter, or no top-level ``version:`` line in it (one
   that starts at the line's first character), is ``no_version``;
5. a second ``version:`` line, or a value that doesn't fullmatch ``VERSION_PATTERN``
   (``[0-9A-Za-z][0-9A-Za-z.+-]{0,31}``), is ``bad_version``. Before the check the value is
   stripped, cut at its first ``#`` that follows a space or a tab (a YAML comment) and stripped
   again, and one pair of matching quotes (``"`` or ``'``) around it is removed.

The snapshot's content is the whole normalised text, front matter included.

**``PlaybookSnapshot(version, content)``** is frozen and ``content`` never shows in its
``repr``. ``version`` fullmatches ``VERSION_PATTERN``; ``content`` is a non-blank ``str`` of at
most ``MAX_PLAYBOOK_CHARS`` characters that encodes as UTF-8 (``TypeError`` or ``ValueError``
naming the field, never its value). ``sha256`` is the hex SHA-256 of the content's UTF-8; ``short``
is its first 12 hex digits.

**``PlaybookProblem``** is ``unreadable`` (``nextron.main`` reads the file; any ``OSError``,
a missing file included), ``too_large``, ``not_utf8``, ``no_version`` or ``bad_version``.
``problem_text(problem)`` is the Operator's sentence for it, ending ", so no snapshot was
stored."; ``no_version``'s adds how to fix it. ``InvalidPlaybook(problem)`` (code
``invalid_playbook``) carries ``.problem`` and that sentence as its message, never Playbook text.

**The frame** (decision 5). ``playbook_section(snapshot)`` is what the gateway puts in the
cached system prompt after a Playbook route's own prompt: ``AUTHORITY_ORDER``, a blank line,
``Sales Playbook, version <version>:``, a blank line, then the content. A contract test pins the
frame's SHA-256: it never changes.

**``SyncTrigger``** says why the Playbook was read: ``startup`` (``serve``; actor
``system:playbook``, cause ``startup_sync``) or ``operator_check`` (the AI content view's "Check
for changes"; actor ``operator``, cause ``operator_check``).
"""

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from nextron.domain.errors import DomainError

VERSION_PATTERN: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}")
MAX_PLAYBOOK_CHARS: Final = 100_000
MAX_PLAYBOOK_BYTES: Final = 4 * MAX_PLAYBOOK_CHARS  # UTF-8 needs at most 4 bytes a character
SHORT_SHA_LENGTH: Final = 12
FRONT_MATTER_FENCE: Final = "---"
VERSION_KEY: Final = "version:"
COMMENT: Final = re.compile(r"[ \t]#")  # a YAML comment starts at a # after whitespace
QUOTES: Final = ("\"", "'")
QUOTED_LENGTH: Final = 2  # a quote at each end
BYTE_ORDER_MARK: Final = "\ufeff"
AUTHORITY_ORDER: Final = (
    "Order of authority: law and platform rules come first, then the Step brief, then the "
    "Sales Playbook. Where they disagree, follow the earlier one."
)
NOT_STORED: Final = ", so no snapshot was stored."


class PlaybookProblem(StrEnum):
    """Why a read of the Sales Playbook gave no snapshot."""

    UNREADABLE = "unreadable"
    TOO_LARGE = "too_large"
    NOT_UTF8 = "not_utf8"
    NO_VERSION = "no_version"
    BAD_VERSION = "bad_version"


PROBLEM_TEXTS: Final = {
    PlaybookProblem.UNREADABLE: f"the file couldn't be read{NOT_STORED}",
    PlaybookProblem.TOO_LARGE: (
        f"the file is longer than {MAX_PLAYBOOK_CHARS:,} characters{NOT_STORED}"
    ),
    PlaybookProblem.NOT_UTF8: f"the file isn't UTF-8 text{NOT_STORED}",
    PlaybookProblem.NO_VERSION: (
        f"its front matter has no version line{NOT_STORED} Add a line such as "
        "\"version: 0.3.0\" between the two --- lines at the top of the file."
    ),
    PlaybookProblem.BAD_VERSION: (
        "its front matter repeats the version line, or the version isn't 1 to 32 letters, "
        f"digits, dots, plus or minus signs starting with a letter or digit{NOT_STORED}"
    ),
}


def problem_text(problem: PlaybookProblem) -> str:
    """The Operator's sentence for ``problem``."""
    if not isinstance(problem, PlaybookProblem):
        raise TypeError("problem must be a PlaybookProblem")
    return PROBLEM_TEXTS[problem]


class InvalidPlaybook(DomainError):
    """A Sales Playbook read that gave no snapshot; the message never holds Playbook text."""

    code = "invalid_playbook"

    def __init__(self, problem: PlaybookProblem) -> None:
        super().__init__(problem_text(problem))
        self.problem = problem


class SyncTrigger(StrEnum):
    """Why the Sales Playbook was read: at ``serve`` start, or the Operator's check."""

    STARTUP = "startup"
    OPERATOR_CHECK = "operator_check"

    @property
    def actor(self) -> str:
        """The ``events`` actor of what this read records."""
        return "system:playbook" if self is SyncTrigger.STARTUP else "operator"

    @property
    def cause(self) -> str:
        """The ``events`` cause of what this read records."""
        return "startup_sync" if self is SyncTrigger.STARTUP else "operator_check"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlaybookSnapshot:
    """One version of the Sales Playbook's text; ``content`` never shows in the ``repr``."""

    version: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str):
            raise TypeError("version must be a str")
        if VERSION_PATTERN.fullmatch(self.version) is None:
            raise ValueError(f"version must match {VERSION_PATTERN.pattern}")
        if not isinstance(self.content, str):
            raise TypeError("content must be a str")
        if not self.content.strip():
            raise ValueError("content must not be blank")
        if len(self.content) > MAX_PLAYBOOK_CHARS:
            raise ValueError(f"content must be at most {MAX_PLAYBOOK_CHARS:,} characters")
        try:
            self.content.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("content must encode as UTF-8") from None

    @property
    def sha256(self) -> str:
        """The hex SHA-256 of the content's UTF-8."""
        return _sha256(self.content)

    @property
    def short(self) -> str:
        """The first 12 hex digits of ``sha256``."""
        return self.sha256[:SHORT_SHA_LENGTH]


def _fence(line: str) -> bool:
    return line.rstrip(" \t") == FRONT_MATTER_FENCE


def _front_matter(text: str) -> list[str] | None:
    """The lines between the first line ``---`` and the next ``---``, or ``None``."""
    lines = text.split("\n")
    if not lines or not _fence(lines[0]):
        return None
    for index in range(1, len(lines)):
        if _fence(lines[index]):
            return lines[1:index]
    return None


def _version_value(raw: str) -> str:
    value = raw.strip()
    comment = COMMENT.search(value)
    if comment is not None:
        value = value[: comment.start()].strip()
    if len(value) >= QUOTED_LENGTH and value[0] == value[-1] and value[0] in QUOTES:
        value = value[1:-1]
    return value


def read_playbook(data: bytes) -> PlaybookSnapshot:
    """The snapshot of the Playbook file's bytes; ``InvalidPlaybook`` otherwise. See the module
    docstring."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > MAX_PLAYBOOK_BYTES:
        raise InvalidPlaybook(PlaybookProblem.TOO_LARGE)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidPlaybook(PlaybookProblem.NOT_UTF8) from None
    text = text.removeprefix(BYTE_ORDER_MARK).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_PLAYBOOK_CHARS:
        raise InvalidPlaybook(PlaybookProblem.TOO_LARGE)
    front = _front_matter(text)
    if front is None:
        raise InvalidPlaybook(PlaybookProblem.NO_VERSION)
    values = [line[len(VERSION_KEY) :] for line in front if line.startswith(VERSION_KEY)]
    if not values:
        raise InvalidPlaybook(PlaybookProblem.NO_VERSION)
    if len(values) > 1:
        raise InvalidPlaybook(PlaybookProblem.BAD_VERSION)
    version = _version_value(values[0])
    if VERSION_PATTERN.fullmatch(version) is None:
        raise InvalidPlaybook(PlaybookProblem.BAD_VERSION)
    return PlaybookSnapshot(version, text)


def playbook_section(snapshot: PlaybookSnapshot) -> str:
    """The framed Playbook text of a pinned snapshot; see the module docstring."""
    if not isinstance(snapshot, PlaybookSnapshot):
        raise TypeError("snapshot must be a PlaybookSnapshot")
    return (
        f"{AUTHORITY_ORDER}\n\nSales Playbook, version {snapshot.version}:\n\n{snapshot.content}"
    )

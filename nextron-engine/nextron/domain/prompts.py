"""Versioned prompt files and their hash lock (FR-71, AD-8, AD-18, NFR-25, Story 2.8). Standard
library only.

Each AI route's system prompt is a file ``nextron/adapters/ai/prompts/<route>/<version>.md``.
Once a version has been used it never changes: a change is a new version. ``manifest.lock`` in
the same folder pins each file by its SHA-256, and ``serve`` refuses to start when a file and
the lock disagree.

**The text.** ``decode_prompt(data, label)`` is a prompt file's text: its bytes decoded as
strict UTF-8, with universal newlines (``\\r\\n`` and a lone ``\\r`` become ``\\n``), so a CRLF
checkout hashes the same as an LF one. Bytes that aren't UTF-8 raise ``InvalidPrompts`` naming
``label``. A text's SHA-256 is ``nextron.domain.ai.text_sha256``, which is
``RouteTuple.prompt_sha256``.

**``manifest.lock``** is plain text: one line ``<route> <version> <sha256>`` per prompt file,
single spaces, the route fullmatching ``budget.CODE_PATTERN``, the version
``ai.PROMPT_VERSION_PATTERN`` and the hash 64 lowercase hex digits. A line starting ``#`` is a
comment, and a blank line is skipped. A leading byte-order mark (U+FEFF, as some editors save
it) is dropped first.

- ``ManifestEntry(line, route, version, sha256)``: one manifest line and its 1-based number.
- ``parse_manifest(text)`` returns the entries in file order. A malformed line, or a second line
  for the same ``(route, version)``, raises ``InvalidPrompts`` naming the line.

**``PromptFile(route, version, text)``** is one prompt file's content. It is frozen, ``text``
never shows in its ``repr``, and ``sha256`` is ``text_sha256(text)``. The route and version
must fullmatch their patterns and the text must be a non-blank ``str`` that encodes as UTF-8
(``TypeError`` or ``ValueError`` naming the field). ``file_name`` is ``<route>/<version>.md``.

**``verify(entries, files)``** checks the files on disk against the manifest and returns the
``PromptFile``s in manifest order. ``files`` maps each ``*.md`` file found at any depth of the
prompts folder, by its ``/``-separated path relative to that folder, to its text (so a stray
``README.md`` at the root or a nested ``a/b/v1.md`` is a misnamed file). It raises
``InvalidPrompts`` for the first problem, in this order:

1. a file whose name isn't ``<route>/v<n>.md`` (the route and version patterns), by name;
2. for each manifest line in order: its file is missing, its text is blank, or its SHA-256
   doesn't match the line's;
3. a file the manifest doesn't list, by name.

**``InvalidPrompts(message)``** (code ``invalid_prompts``) is a ``DomainError``. Its message
names the manifest line, the file, the route or the version, never any prompt text.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from nextron.domain.ai import PROMPT_VERSION_PATTERN, text_sha256
from nextron.domain.budget import CODE_PATTERN
from nextron.domain.errors import DomainError

MANIFEST_FILE_NAME: Final = "manifest.lock"
PROMPT_SUFFIX: Final = ".md"
SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
MANIFEST_LINE: Final = re.compile(
    rf"(?P<route>{CODE_PATTERN.pattern}) (?P<version>{PROMPT_VERSION_PATTERN.pattern}) "
    rf"(?P<sha256>{SHA256_PATTERN.pattern})"
)  # matched with fullmatch
FILE_NAME: Final = re.compile(
    rf"(?P<route>{CODE_PATTERN.pattern})/(?P<version>{PROMPT_VERSION_PATTERN.pattern})\.md"
)  # matched with fullmatch
COMMENT: Final = "#"
USED_PROMPT_NEVER_CHANGES: Final = "a used prompt never changes: add a new version"


class InvalidPrompts(DomainError):
    """Prompt files and ``manifest.lock`` that disagree, or a prompt that can't be used; the
    message never holds prompt text."""

    code = "invalid_prompts"


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One ``manifest.lock`` line: ``<route> <version> <sha256>``, and its 1-based number."""

    line: int
    route: str
    version: str
    sha256: str

    @property
    def file_name(self) -> str:
        """``<route>/<version>.md``."""
        return f"{self.route}/{self.version}{PROMPT_SUFFIX}"


@dataclass(frozen=True, slots=True)
class PromptFile:
    """One prompt file's route, version and text."""

    route: str
    version: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        for name, value, pattern in (
            ("route", self.route, CODE_PATTERN),
            ("version", self.version, PROMPT_VERSION_PATTERN),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a str")
            if pattern.fullmatch(value) is None:
                raise ValueError(f"{name} must match {pattern.pattern}")
        if not isinstance(self.text, str):
            raise TypeError("text must be a str")
        try:
            self.text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must encode as UTF-8") from None
        if not self.text.strip():
            raise ValueError("text must not be blank")

    @property
    def sha256(self) -> str:
        """The hex SHA-256 of the text's UTF-8 (``RouteTuple.prompt_sha256``)."""
        return text_sha256(self.text)

    @property
    def file_name(self) -> str:
        """``<route>/<version>.md``."""
        return f"{self.route}/{self.version}{PROMPT_SUFFIX}"


def decode_prompt(data: bytes, label: str) -> str:
    """``data`` as strict UTF-8 with universal newlines; ``InvalidPrompts`` naming ``label``
    when it isn't UTF-8."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidPrompts(f"{label}: the file isn't valid UTF-8") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_manifest(text: str) -> tuple[ManifestEntry, ...]:
    """The entries of ``manifest.lock``'s ``text``, in file order; see the module docstring."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    entries: list[ManifestEntry] = []
    seen: dict[tuple[str, str], int] = {}
    text = text.removeprefix("﻿")  # a byte-order mark some editors save
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith(COMMENT):
            continue
        found = MANIFEST_LINE.fullmatch(line)
        if found is None:
            raise InvalidPrompts(
                f"{MANIFEST_FILE_NAME} line {number}: expected '<route> <version> <sha256>', "
                "with single spaces and a lowercase hex SHA-256"
            )
        route, version = found["route"], found["version"]
        first = seen.get((route, version))
        if first is not None:
            raise InvalidPrompts(
                f"{MANIFEST_FILE_NAME} line {number}: {route} {version} is already listed on "
                f"line {first}"
            )
        seen[(route, version)] = number
        entries.append(ManifestEntry(number, route, version, found["sha256"]))
    return tuple(entries)


def verify(entries: Sequence[ManifestEntry], files: Mapping[str, str]) -> tuple[PromptFile, ...]:
    """The prompt files ``entries`` list, checked against ``files``; see the module
    docstring."""
    chosen = tuple(entries)
    if not all(isinstance(entry, ManifestEntry) for entry in chosen):
        raise TypeError("entries must be ManifestEntry values")
    if not isinstance(files, Mapping) or not all(
        isinstance(name, str) and isinstance(text, str) for name, text in files.items()
    ):
        raise TypeError("files must map file names to texts")
    keys = Counter((entry.route, entry.version) for entry in chosen)
    repeated = sorted(f"{route} {version}" for (route, version), n in keys.items() if n > 1)
    if repeated:
        raise InvalidPrompts(f"{MANIFEST_FILE_NAME} lists {repeated[0]} more than once")
    for name in sorted(files):
        if FILE_NAME.fullmatch(name) is None:
            raise InvalidPrompts(
                f"{name}: a prompt file must be named <route>/v<n>.md (a lowercase route and a "
                "version from v1 to v9999)"
            )
    found: list[PromptFile] = []
    for entry in chosen:
        where = f"{MANIFEST_FILE_NAME} line {entry.line}: {entry.route} {entry.version}"
        text = files.get(entry.file_name)
        if text is None:
            raise InvalidPrompts(f"{where}: the file {entry.file_name} is missing")
        if not text.strip():
            raise InvalidPrompts(f"{where}: the file {entry.file_name} is blank")
        if text_sha256(text) != entry.sha256:
            raise InvalidPrompts(
                f"{where}: the SHA-256 of {entry.file_name} doesn't match; "
                f"{USED_PROMPT_NEVER_CHANGES}"
            )
        found.append(PromptFile(entry.route, entry.version, text))
    listed = {entry.file_name for entry in chosen}
    for name in sorted(files):
        if name not in listed:
            raise InvalidPrompts(f"{name}: the file isn't listed in {MANIFEST_FILE_NAME}")
    return tuple(found)

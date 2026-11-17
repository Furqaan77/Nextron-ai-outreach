"""A strict reader for a subset of YAML: the seed files (AD-12, Story 2.1, decision 1).

The engine pins no YAML library, so the seed files are written in a subset this module reads
with the standard library alone. Everything outside the subset is refused with its line number,
so a seed file is never misread: any text ``parse`` accepts gives the value a YAML 1.1 or 1.2
reader gives, and swapping in a YAML library later changes no seed file.

``parse(text)`` returns the top-level ``dict`` or ``list``. A problem raises
``SeedSyntaxError(line, reason)`` with the 1-based ``line`` (0 when the problem isn't on one
line) and the message ``"line <n>: <reason>"``. A reason never repeats more than 40 characters
of the input.

**What it accepts:**

- One optional leading byte order mark, and ``\\n`` or ``\\r\\n`` line ends.
- Comments: ``#`` at the start of a line's content, or after a space, outside quotes, to the end
  of the line. Blank and comment-only lines are skipped.
- Indentation by spaces. A nested block is indented more than its parent; siblings share one
  column.
- Block mappings: ``key: value``, or ``key:`` followed by a more-indented block. A key
  fullmatches ``[a-z][a-z0-9_]{0,63}`` and appears once per mapping.
- Block sequences, indented under their key: ``- value``; ``- key: value``, which starts a
  mapping whose further keys sit at the column of the first key; and ``-`` alone, followed by a
  more-indented block.
- Flow sequences of scalars on one line: ``[a, 'b, c', "d"]``, and ``[]`` for an empty list.
- Single-quoted scalars (``''`` is a quote) and double-quoted scalars (the only escapes are
  ``\\\\`` and ``\\"``), each closed on its own line.
- Plain scalars, trimmed: ``-?(0|[1-9][0-9]*)`` is an ``int``, ``true`` and ``false`` are
  ``bool``, and anything else is a ``str``.

**What it refuses**, naming the line and the fix ("write it in quotes" where quoting helps):
tabs, any ``\\r`` but one before ``\\n``, other control characters, line and paragraph
separators, a byte order mark after the start, surrogates and the noncharacters U+FFFE and
U+FFFF; document markers (``---``, ``...``); anchors, aliases and tags (``&``, ``*``, ``!``);
flow mappings (``{``); block scalars (``|``, ``>``); ``?`` keys; directives (``%``); a plain
scalar containing ``: ``, ending in ``:``, starting with ``- `` (or ``-`` alone), or starting
with any of ``[]{}&*!|>%@`,?:#`` (in a flow list an item also may not contain ``[``, ``]``,
``{``, ``}``, ``,`` or ``?``, which YAML 1.1 ends a plain item at); nested flow lists, an empty
item and a trailing comma; multi-line scalars; ``key:`` with no value and no nested block; a
list under a key at the key's own column; a top-level scalar, and an empty or comment-only
file; nesting deeper than 32 levels; a key another reader reads as a boolean or null (``on``,
``no``, ``null`` and so on); and any plain scalar another YAML reader could read as something
other than a string, apart from the subset's own ints and booleans: a fullmatch of
``[-+]?\\.?[0-9][0-9A-Za-z_.:+-]*`` (numbers, dates, times; this includes the spec's
``[-+.]?[0-9][0-9A-Za-z_.:+-]*`` and also YAML 1.1's ``+.5``), a fullmatch of the YAML 1.1
float ``[-+]?([0-9][0-9_]*)?\\.[0-9.]*([eE][-+][0-9]+)?`` (which also types ``.``, ``-.``,
``+.``, ``..``, ``.e+5`` and ``.E-1`` as floats), a date followed by a space or
``T`` (a YAML 1.1 timestamp may hold spaces), ``.inf`` and ``.nan`` in any case or sign, ``=``
and ``<<`` (YAML 1.1's value and merge keys), and ``y``, ``n``, ``yes``, ``no``, ``on``,
``off``, ``true``, ``false``, ``null`` and ``~`` in any case other than the accepted
``true``/``false``.
"""

import re
from dataclasses import dataclass
from typing import Final

from nextron.domain.errors import DomainError

MAX_DEPTH: Final = 32  # nesting levels; the top-level mapping or list is level 1
SHOWN: Final = 40  # at most this many characters of the input in a reason
BOM: Final = "\ufeff"

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # matched with fullmatch
_KEY_LINE: Final = re.compile(f"({KEY_PATTERN.pattern}):(?= |$)")  # matched at the start
_KEY_NO_SPACE: Final = re.compile(f"{KEY_PATTERN.pattern}:")  # "key:value"
INT_PATTERN: Final = re.compile(r"-?(?:0|[1-9][0-9]*)")  # matched with fullmatch
NUMBER_LIKE: Final = re.compile(r"[-+]?\.?[0-9][0-9A-Za-z_.:+-]*")  # matched with fullmatch
# The YAML 1.1 spec's float, which also matches ".", "+.", ".." and ".e+5"; fullmatch.
YAML11_FLOAT: Final = re.compile(r"[-+]?(?:[0-9][0-9_]*)?\.[0-9.]*(?:[eE][-+][0-9]+)?")
SPECIAL_FLOAT: Final = re.compile(r"[-+]?\.(?:inf|nan)", re.IGNORECASE)  # fullmatch
DATE_TIME_LIKE: Final = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}[Tt ]")  # at the start
RESERVED_WORDS: Final = frozenset(
    {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"}
)
YAML11_KEYS: Final = frozenset({"=", "<<"})
BOOLEANS: Final = {"true": True, "false": False}
PLAIN_START_REFUSED: Final = frozenset("[]{}&*!|>%@`,?:#")
FLOW_ITEM_REFUSED: Final = frozenset("[]{},?")
# Characters refused anywhere: control characters (Cc) but the line feed, line and paragraph
# separators (Zl, Zp), a byte order mark (only one leading BOM is stripped), surrogates and
# the noncharacters U+FFFE and U+FFFF, none of which YAML treats as plain content.
_FORBIDDEN: Final = re.compile(
    "[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029\ufeff\ud800-\udfff\ufffe\uffff]"
)

QUOTE_IT: Final = "write it in quotes"
TOO_DEEP: Final = f"nesting deeper than {MAX_DEPTH} levels isn't supported"


class SeedSyntaxError(DomainError):
    """A seed file outside the YAML subset. ``line`` is 1-based, or 0 when the problem isn't on
    one line; ``reason`` says what to change and repeats at most 40 characters of the input."""

    code = "seed_syntax_error"

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")
        self.line = line
        self.reason = reason


def shown(text: str) -> str:
    """``text`` quoted for a reason, cut so that at most ``SHOWN`` characters of it appear."""
    if len(text) > SHOWN:
        text = text[: SHOWN - 3] + "..."
    return f"'{text}'"


# Characters and lines -------------------------------------------------------------------------


def _character_problem(char: str) -> str:
    if char == "\t":
        return "tabs aren't allowed: indent and separate with spaces"
    if char == "\r":
        return "a carriage return is allowed only right before a line feed"
    if char == BOM:
        return "a byte order mark is allowed only at the very start of the file"
    if char in "\u2028\u2029":
        return f"the line or paragraph separator U+{ord(char):04X} isn't allowed"
    return f"the character U+{ord(char):04X} isn't allowed"


def _lines(text: str) -> list[tuple[int, str]]:
    """Every physical line as ``(number, text)``, without its line end, after the checks."""
    if text.startswith(BOM):
        text = text[1:]
    segments = text.split("\n")
    last = len(segments) - 1
    lines = []
    for index, segment in enumerate(segments):
        if index < last and segment.endswith("\r"):
            segment = segment[:-1]
        found = _FORBIDDEN.search(segment)
        if found is not None:
            raise SeedSyntaxError(index + 1, _character_problem(found.group()))
        lines.append((index + 1, segment))
    return lines


# Scalars -------------------------------------------------------------------------------------


def _is_document_marker(text: str) -> bool:
    return text in ("---", "...") or text.startswith(("--- ", "... "))


def _start_problem(char: str) -> str:
    if char in "&*!":
        return f"anchors, aliases and tags (&, *, !) aren't supported: {QUOTE_IT}"
    if char in "{}":
        return f"flow mappings ({{...}}) aren't supported: {QUOTE_IT}"
    if char in "|>":
        return f"block scalars (| and >) aren't supported: {QUOTE_IT}, on one line"
    if char == "?":
        return f"? keys aren't supported: {QUOTE_IT}"
    if char == "%":
        return f"directives (%) aren't supported: {QUOTE_IT}"
    return f"a value may not start with {char}: {QUOTE_IT}"


def _plain_value(text: str, line: int, *, in_flow: bool) -> object:
    """The value of the trimmed plain scalar ``text``, or ``SeedSyntaxError``."""
    if text[0] in PLAIN_START_REFUSED:
        if in_flow and text[0] == "[":
            raise SeedSyntaxError(line, "nested lists aren't supported")
        raise SeedSyntaxError(line, _start_problem(text[0]))
    if text == "-" or text.startswith("- "):
        raise SeedSyntaxError(
            line, f"a value may not start with '- ' (a list goes on its own lines): {QUOTE_IT}"
        )
    if _is_document_marker(text):
        raise SeedSyntaxError(line, f"document markers (--- and ...) aren't supported: {QUOTE_IT}")
    if ": " in text or text.endswith(":"):
        raise SeedSyntaxError(line, f"a value may not contain ': ' or end with ':': {QUOTE_IT}")
    if in_flow and any(char in FLOW_ITEM_REFUSED for char in text):
        raise SeedSyntaxError(
            line, f"a list item may not contain [ ] {{ }} , or ?: {QUOTE_IT}"
        )
    if INT_PATTERN.fullmatch(text):
        try:
            return int(text)
        except ValueError:  # beyond Python's digit limit for int()
            raise SeedSyntaxError(line, f"this number is too long to read: {QUOTE_IT}") from None
    if text in BOOLEANS:
        return BOOLEANS[text]
    if text.lower() in RESERVED_WORDS:
        raise SeedSyntaxError(
            line, f"another YAML reader would read this as a boolean or null: {QUOTE_IT}"
        )
    if (
        NUMBER_LIKE.fullmatch(text)
        or YAML11_FLOAT.fullmatch(text)
        or SPECIAL_FLOAT.fullmatch(text)
        or DATE_TIME_LIKE.match(text)
        or text in YAML11_KEYS
    ):
        raise SeedSyntaxError(
            line,
            f"another YAML reader would read this as a number, date or time, not text: "
            f"{QUOTE_IT}",
        )
    return text


def _single_quoted(text: str, line: int) -> tuple[str, str]:
    """``text`` starts with ``'``: the scalar and the rest of the line after it."""
    parts = []
    start = 1
    while True:
        end = text.find("'", start)
        if end < 0:
            raise SeedSyntaxError(line, "unclosed quote: close it on the same line")
        parts.append(text[start:end])
        if text.startswith("''", end):
            parts.append("'")
            start = end + 2
            continue
        return "".join(parts), text[end + 1 :]


def _double_quoted(text: str, line: int) -> tuple[str, str]:
    """``text`` starts with ``"``: the scalar and the rest of the line after it."""
    parts = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == '"':
            return "".join(parts), text[index + 1 :]
        if char == "\\":
            escaped = text[index + 1 : index + 2]
            if escaped not in ("\\", '"'):
                raise SeedSyntaxError(
                    line, 'the only escapes in double quotes are \\\\ and \\": use single quotes'
                )
            parts.append(escaped)
            index += 2
            continue
        parts.append(char)
        index += 1
    raise SeedSyntaxError(line, "unclosed quote: close it on the same line")


def _comment_at(text: str, start: int) -> int:
    """The index of the first ``#`` after a space in ``text`` from ``start``, or ``len``."""
    index = text.find(" #", start)
    return len(text) if index < 0 else index


def _check_rest(rest: str, line: int) -> None:
    """After a value only spaces may follow, then a comment that starts after a space."""
    stripped = rest.lstrip(" ")
    if not stripped:
        return
    if stripped.startswith("#") and len(stripped) < len(rest):
        return
    raise SeedSyntaxError(
        line, "only a comment (after a space) may follow a value on the same line"
    )


def _flow_list(text: str, line: int) -> tuple[list[object], str]:
    """``text`` starts with ``[``: the list of scalars and the rest of the line after ``]``."""
    unclosed = "unclosed [: a list in brackets must close on the same line"
    items: list[object] = []
    index = 1
    expecting_item = True
    while True:
        while index < len(text) and text[index] == " ":
            index += 1
        if index >= len(text) or (text[index] == "#" and text[index - 1] == " "):
            raise SeedSyntaxError(line, unclosed)
        char = text[index]
        if not expecting_item:
            if char == "]":
                return items, text[index + 1 :]
            if char != ",":
                raise SeedSyntaxError(line, "separate list items with ', '")
            index += 1
            expecting_item = True
            continue
        if char == "]":
            if items:
                raise SeedSyntaxError(line, "a trailing comma isn't allowed in a list")
            return items, text[index + 1 :]
        if char == ",":
            raise SeedSyntaxError(line, "an empty list item isn't allowed")
        if char == "'":
            value, rest = _single_quoted(text[index:], line)
            items.append(value)
            index = len(text) - len(rest)
        elif char == '"':
            value, rest = _double_quoted(text[index:], line)
            items.append(value)
            index = len(text) - len(rest)
        else:
            end = index
            while end < len(text) and text[end] not in ",]":
                if text[end] == "#" and text[end - 1] == " ":
                    break
                end += 1
            raw = text[index:end].rstrip(" ")
            if end >= len(text) or text[end] == "#":
                if raw and raw[0] in "[{":
                    raise SeedSyntaxError(line, _nested_problem(raw[0]))
                raise SeedSyntaxError(line, unclosed)
            items.append(_plain_value(raw, line, in_flow=True))
            index = end
        expecting_item = False


def _nested_problem(char: str) -> str:
    if char == "[":
        return "nested lists aren't supported"
    return f"flow mappings ({{...}}) aren't supported: {QUOTE_IT}"


def _value(text: str, line: int) -> object:
    """The value that starts ``text`` (a non-blank line rest, not a comment), checking that
    only a comment follows it."""
    first = text[0]
    if first == "'":
        value, rest = _single_quoted(text, line)
    elif first == '"':
        value, rest = _double_quoted(text, line)
    elif first == "[":
        value, rest = _flow_list(text, line)
    else:
        end = _comment_at(text, 0)
        return _plain_value(text[:end].rstrip(" "), line, in_flow=False)
    _check_rest(rest, line)
    return value


# Lines into tokens -----------------------------------------------------------------------------

_NO_VALUE: Final = object()  # "key:" or "-" with nothing after it on the line


@dataclass(frozen=True, slots=True)
class _Token:
    """One ``key:`` or ``-`` on a line. ``inline`` marks a ``-`` whose mapping starts on the
    same line (``- key: value``): the key's own token follows it."""

    kind: str  # "key" or "item"
    column: int
    line: int
    key: str
    value: object
    inline: bool = False


def _line_problem(content: str, column: int, previous: _Token | None) -> str:
    """Why a line that is neither ``key: ...`` nor ``- ...`` is refused."""
    first = content[0]
    if first in "{&*!|>?%":
        return _start_problem(first)
    if previous is not None and column > previous.column:
        if previous.value is _NO_VALUE:
            return "write the value on the same line as its key or '-'"
        return "multi-line values aren't supported: write the value on one line"
    if _KEY_NO_SPACE.match(content):
        return "put a space after the ':'"
    if ":" in content:
        return (
            "a key must be lowercase letters, digits and underscores, start with a letter, "
            "have at most 64 characters and be followed by ': ' or the line end"
        )
    if previous is None:
        return "the top level must be a mapping (key: value) or a list (- item)"
    return "expected 'key: value' or '- item'"


def _key_tokens(content: str, column: int, line: int, tokens: list[_Token]) -> None:
    """``content`` starts with ``key:`` followed by a space or the line end."""
    match = _KEY_LINE.match(content)
    assert match is not None
    key = match.group(1)
    if key in RESERVED_WORDS:
        raise SeedSyntaxError(
            line, "another YAML reader would read this key as a boolean or null: rename it"
        )
    rest = content[match.end() :].lstrip(" ")
    value = _NO_VALUE if not rest or rest.startswith("#") else _value(rest, line)
    tokens.append(_Token("key", column, line, key, value))


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for line, raw in _lines(text):
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            continue
        column = len(raw) - len(content)
        if _is_document_marker(content):
            raise SeedSyntaxError(line, "document markers (--- and ...) aren't supported")
        if content == "-" or content.startswith("- "):
            after = content[1:]
            rest = after.lstrip(" ")
            if not rest or rest.startswith("#"):
                tokens.append(_Token("item", column, line, "", _NO_VALUE))
            elif _KEY_LINE.match(rest):
                tokens.append(_Token("item", column, line, "", _NO_VALUE, inline=True))
                _key_tokens(rest, column + 1 + len(after) - len(rest), line, tokens)
            else:
                tokens.append(_Token("item", column, line, "", _value(rest, line)))
        elif _KEY_LINE.match(content):
            _key_tokens(content, column, line, tokens)
        else:
            previous = tokens[-1] if tokens else None
            raise SeedSyntaxError(line, _line_problem(content, column, previous))
    return tokens


# Tokens into values ----------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._position = 0

    def peek(self) -> _Token | None:
        if self._position < len(self._tokens):
            return self._tokens[self._position]
        return None

    def block(self, depth: int) -> dict[str, object] | list[object]:
        token = self._tokens[self._position]
        if depth > MAX_DEPTH:
            raise SeedSyntaxError(token.line, TOO_DEEP)
        if token.kind == "key":
            return self._mapping(token.column, depth)
        return self._sequence(token.column, depth)

    def _misaligned(self, column: int) -> None:
        following = self.peek()
        if following is not None and following.column > column:
            raise SeedSyntaxError(
                following.line, "bad indentation: this line lines up with no block above it"
            )

    def _mapping(self, column: int, depth: int) -> dict[str, object]:
        result: dict[str, object] = {}
        while (token := self.peek()) is not None and token.column == column:
            if token.kind != "key":
                raise SeedSyntaxError(token.line, "a list item where a key was expected")
            self._position += 1
            if token.key in result:
                raise SeedSyntaxError(token.line, f"repeated key {shown(token.key)}")
            result[token.key] = self._value_of(token, depth)
            self._misaligned(column)
        return result

    def _sequence(self, column: int, depth: int) -> list[object]:
        result: list[object] = []
        while (token := self.peek()) is not None and token.column == column:
            if token.kind != "item":
                raise SeedSyntaxError(token.line, "a key where a list item was expected")
            self._position += 1
            if token.inline:
                result.append(self.block(depth + 1))
            else:
                result.append(self._value_of(token, depth))
            self._misaligned(column)
        return result

    def _value_of(self, token: _Token, depth: int) -> object:
        """The value of ``token``'s entry, at ``depth`` (its container's level)."""
        following = self.peek()
        if token.value is not _NO_VALUE:
            if isinstance(token.value, list) and depth + 1 > MAX_DEPTH:
                raise SeedSyntaxError(token.line, TOO_DEEP)
            if following is not None and following.column > token.column:
                raise SeedSyntaxError(
                    following.line,
                    "unexpected indentation: the line above already has its value "
                    "(multi-line values aren't supported)",
                )
            return token.value
        if following is None or following.column <= token.column:
            if (
                following is not None
                and token.kind == "key"
                and following.kind == "item"
                and following.column == token.column
            ):
                raise SeedSyntaxError(following.line, "indent the list under its key")
            what = "this key" if token.kind == "key" else "'-'"
            raise SeedSyntaxError(
                token.line, f"{what} has no value and no more-indented block under it"
            )
        return self.block(depth + 1)


def parse(text: str) -> dict[str, object] | list[object]:
    """The value of the seed file ``text``; see the module docstring. Raises
    ``SeedSyntaxError`` for anything outside the subset."""
    if not isinstance(text, str):
        raise TypeError(f"parse takes a str, not {type(text).__name__}")
    tokens = _tokens(text)
    if not tokens:
        raise SeedSyntaxError(0, "the file is empty: it needs a mapping (key: value) or a list")
    parser = _Parser(tokens)
    value = parser.block(1)
    following = parser.peek()
    if following is not None:
        raise SeedSyntaxError(
            following.line, "bad indentation: this line lines up with no block above it"
        )
    return value

"""The seed files' YAML-subset reader (``nextron/domain/seed_yaml.py``, Story 2.1, decision 1).

Every accepted form is checked with its exact value, and every refusal with its line number.
The values are the ones a YAML 1.1 or 1.2 reader gives for the same text, so the subset never
misreads.
"""

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.seed_yaml import MAX_DEPTH, SHOWN, SeedSyntaxError, parse

# Accepted forms ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("a: b\n", {"a": "b"}),
        ("a: b", {"a": "b"}),  # no final line end
        ("a: b\nc: d\n", {"a": "b", "c": "d"}),
        ("a:\n  b: c\n  d: e\nf: g\n", {"a": {"b": "c", "d": "e"}, "f": "g"}),
        ("a:\n    b:\n        c: d\n", {"a": {"b": {"c": "d"}}}),
        ("- a\n- b\n", ["a", "b"]),
        ("a:\n  - b\n  - c\n", {"a": ["b", "c"]}),
        ("- key: a\n  other: b\n- key: c\n", [{"key": "a", "other": "b"}, {"key": "c"}]),
        ("-   key: a\n    other: b\n", [{"key": "a", "other": "b"}]),
        ("- key:\n    - x\n  other: 1\n", [{"key": ["x"], "other": 1}]),
        ("-\n  a: 1\n-\n  - b\n", [{"a": 1}, ["b"]]),
        ("- # a comment\n  a: 1\n", [{"a": 1}]),
        ("a: # a comment\n  b: 1\n", {"a": {"b": 1}}),
        ("  a: 1\n  b: 2\n", {"a": 1, "b": 2}),  # an indented top level
        ("a: [x, 'y, z', \"q\"]\n", {"a": ["x", "y, z", "q"]}),
        ("a: [x,z]\n", {"a": ["x", "z"]}),
        ("a: [ x , z ]\n", {"a": ["x", "z"]}),
        ("a: []\n", {"a": []}),
        ("a: [ ]\n", {"a": []}),
        ("a: [1, true, -3, many words]\n", {"a": [1, True, -3, "many words"]}),
        ("a: [a#b, 'c # d']\n", {"a": ["a#b", "c # d"]}),
        ("- [a, b]\n- []\n", [["a", "b"], []]),
        ("a: 'it''s'\n", {"a": "it's"}),
        ("a: ''\n", {"a": ""}),
        ("a: ' padded '\n", {"a": " padded "}),
        ("a: 'yes'\n", {"a": "yes"}),
        ("a: '1.5'\n", {"a": "1.5"}),
        ("a: 'a: b # c'\n", {"a": "a: b # c"}),
        ("a: 'back\\slash'\n", {"a": "back\\slash"}),
        ('a: "say \\"hi\\""\n', {"a": 'say "hi"'}),
        ('a: "back\\\\slash"\n', {"a": "back\\slash"}),
        ('a: "it\'s"\n', {"a": "it's"}),
        ("a: 0\n", {"a": 0}),
        ("a: 7\n", {"a": 7}),
        ("a: -12\n", {"a": -12}),
        ("a: 200000000\n", {"a": 200000000}),
        ("a: 123456789012345678901234567890\n", {"a": 123456789012345678901234567890}),
        ("a: true\nb: false\n", {"a": True, "b": False}),
        ("a: plain words\n", {"a": "plain words"}),
        ("a: plain   inner  spaces   \n", {"a": "plain   inner  spaces"}),
        ("a: it's fine\n", {"a": "it's fine"}),
        ("a: say \"hi\"\n", {"a": 'say "hi"'}),
        ("a: a#b\n", {"a": "a#b"}),
        ("a: value # a comment\n", {"a": "value"}),
        ("a: value #\n", {"a": "value"}),
        ("a: x:y\n", {"a": "x:y"}),
        ("a: http://example.com/x\n", {"a": "http://example.com/x"}),
        ("a: -x\n", {"a": "-x"}),
        ("a: a, b, c\n", {"a": "a, b, c"}),
        ("a: a [b] {c}\n", {"a": "a [b] {c}"}),
        ("a: AP/AR\n", {"a": "AP/AR"}),
        ("a: 1 lawyer\n", {"a": "1 lawyer"}),
        ("a: 5–50 attorneys\n", {"a": "5–50 attorneys"}),
        ("a: Hiring intake, legal-ops, or staff\n", {"a": "Hiring intake, legal-ops, or staff"}),
        ("a: x?y\n", {"a": "x?y"}),
        ("a: a ? b\n", {"a": "a ? b"}),
        ("a: Truely\n", {"a": "Truely"}),
        ("a: none\n", {"a": "none"}),
        ("a: .x\n", {"a": ".x"}),
        ("a: café\n", {"a": "café"}),
        ("a: non\u00a0breaking\n", {"a": "non\u00a0breaking"}),
        ("# a comment\n\n   \na: b  # trailing\n# end\n", {"a": "b"}),
        ("a: b\n  # an indented comment\nc: d\n", {"a": "b", "c": "d"}),
        ("a_1: x\nb2_c: z\n", {"a_1": "x", "b2_c": "z"}),
        ("a" * 64 + ": x\n", {"a" * 64: "x"}),
    ],
)
def test_every_accepted_form_gives_its_exact_value(text: str, value: object) -> None:
    result = parse(text)

    assert result == value
    assert type(result) is type(value)


def test_ints_and_booleans_are_typed_and_text_stays_text() -> None:
    result = parse("a: 1\nb: true\nc: '1'\nd: 'true'\n")

    assert result == {"a": 1, "b": True, "c": "1", "d": "true"}
    assert isinstance(result, dict)
    assert type(result["a"]) is int
    assert type(result["b"]) is bool
    assert type(result["c"]) is str


def test_a_leading_byte_order_mark_is_skipped() -> None:
    assert parse("\ufeffa: b\n") == {"a": "b"}


def test_crlf_line_ends_are_read_like_lf() -> None:
    assert parse("a:\r\n  - b # c\r\n  - 'd'\r\ne: [f]\r\n") == {"a": ["b", "d"], "e": ["f"]}


def test_a_bom_and_crlf_together() -> None:
    assert parse("\ufeff- a\r\n- b\r\n") == ["a", "b"]


def test_the_error_is_a_domain_error_with_line_and_reason() -> None:
    with pytest.raises(SeedSyntaxError) as caught:
        parse("a: b\nc: yes\n")

    error = caught.value
    assert isinstance(error, DomainError)
    assert error.code == "seed_syntax_error"
    assert error.line == 2
    assert error.message == f"line 2: {error.reason}"
    assert str(error) == error.message
    assert "write it in quotes" in error.reason


def test_parse_takes_only_text() -> None:
    with pytest.raises(TypeError):
        parse(b"a: b\n")  # type: ignore[arg-type]


# Refusals ---------------------------------------------------------------------------------------


def refusal(text: str) -> SeedSyntaxError:
    with pytest.raises(SeedSyntaxError) as caught:
        parse(text)
    return caught.value


# The I/O matrix's Syntax row.
@pytest.mark.parametrize(
    ("text", "line", "needle"),
    [
        ("a: b\n\tc: d\n", 2, "tabs aren't allowed"),
        ("a: b\nc: x\td\n", 2, "tabs aren't allowed"),
        ("a: b\nc: &anchor x\n", 2, "anchors, aliases and tags"),
        ("a: {b: 1}\n", 1, "flow mappings"),
        ("a: b\nkey: 1.5\n", 2, "number, date or time"),
        ("key: yes\n", 1, "boolean or null"),
        ("a: 1\nb: 2\na: 3\n", 3, "repeated key 'a'"),
        ("a: b\nc: 'unclosed\n", 2, "unclosed quote"),
        ('a: "unclosed\n', 1, "unclosed quote"),
    ],
)
def test_the_syntax_rows_are_refused_with_their_line(text: str, line: int, needle: str) -> None:
    error = refusal(text)

    assert error.line == line
    assert needle in error.reason


@pytest.mark.parametrize(
    ("text", "line", "needle"),
    [
        # characters
        ("a: b\rc: d\n", 1, "carriage return"),
        ("a: b\n\rc: d\n", 2, "carriage return"),  # a CR not before a line feed
        ("a: b\r", 1, "carriage return"),  # a CR at the very end
        ("a: b\x07\n", 1, "U+0007"),
        ("a: b\x00\n", 1, "U+0000"),
        ("a: b\x7f\n", 1, "U+007F"),
        ("a: b\x85c\n", 1, "U+0085"),  # NEL is a line break in YAML 1.1
        ("a: b\u2028c\n", 1, "U+2028"),
        ("a: b\u2029c\n", 1, "U+2029"),
        ("a: b\nc: d\ufeff\n", 2, "byte order mark"),
        ("\ufeff\ufeffa: b\n", 1, "byte order mark"),
        ("a: \ufffe\n", 1, "U+FFFE"),
        ("a: \ud800\n", 1, "U+D800"),
        # document markers
        ("---\na: 1\n", 1, "document markers"),
        ("a: 1\n...\n", 2, "document markers"),
        ("--- a: 1\n", 1, "document markers"),
        ("a: ---\n", 1, "document markers"),
        ("a: ... more\n", 1, "document markers"),
        ("- ...\n", 1, "document markers"),
        # anchors, aliases and tags
        ("a: &x b\n", 1, "anchors, aliases and tags"),
        ("a: *x\n", 1, "anchors, aliases and tags"),
        ("a: !tag b\n", 1, "anchors, aliases and tags"),
        ("a: !!str b\n", 1, "anchors, aliases and tags"),
        ("&x a: b\n", 1, "anchors, aliases and tags"),
        ("- *x\n", 1, "anchors, aliases and tags"),
        ("a:\n  &x\n", 2, "anchors, aliases and tags"),
        # flow mappings
        ("{a: 1}\n", 1, "flow mappings"),
        ("- {a: 1}\n", 1, "flow mappings"),
        ("a: [{b: 1}]\n", 1, "flow mappings"),
        ("a: }\n", 1, "flow mappings"),
        # block scalars
        ("a: |\n  text\n", 1, "block scalars"),
        ("a: >\n  text\n", 1, "block scalars"),
        ("a: |-\n", 1, "block scalars"),
        ("- >\n", 1, "block scalars"),
        ("a:\n  |\n", 2, "block scalars"),
        # ? keys and directives
        ("? a\n: b\n", 1, "? keys"),
        ("a: ? b\n", 1, "? keys"),
        ("%YAML 1.2\na: b\n", 1, "directives"),
        ("a: %x\n", 1, "directives"),
        # plain scalars that YAML reads differently or refuses
        ("a: b: c\n", 1, "': '"),
        ("a: b:\n", 1, "end with ':'"),
        ("- a b: c\n", 1, "': '"),
        ("a: - b\n", 1, "'- '"),
        ("a: -\n", 1, "'- '"),
        ("- - a\n", 1, "'- '"),
        ("- -\n", 1, "'- '"),
        ("a: ]\n", 1, "may not start with ]"),
        ("a: @x\n", 1, "may not start with @"),
        ("a: `x\n", 1, "may not start with `"),
        ("a: ,x\n", 1, "may not start with ,"),
        ("a: :x\n", 1, "may not start with :"),
        # flow lists
        ("a: [[b]]\n", 1, "nested lists"),
        ("a: [b, [c]]\n", 1, "nested lists"),
        ("a: [b, ]\n", 1, "trailing comma"),
        ("a: [b,]\n", 1, "trailing comma"),
        ("a: [,]\n", 1, "empty list item"),
        ("a: [b,,c]\n", 1, "empty list item"),
        ("a: [b\n", 1, "unclosed ["),
        ("a: [b, 'c'\n", 1, "unclosed ["),
        ("a: [b # c]\n", 1, "unclosed ["),
        ("a: [b,\n  c]\n", 1, "unclosed ["),
        ("a: ['b' 'c']\n", 1, "separate list items"),
        ("a: [b] c\n", 1, "only a comment"),
        ("a: [b: c]\n", 1, "': '"),
        ("a: [b:]\n", 1, "end with ':'"),
        ("a: [b?c]\n", 1, "may not contain"),
        ("a: [b}]\n", 1, "may not contain"),
        ("a: [?b]\n", 1, "? keys"),
        ("a: [#b]\n", 1, "may not start with #"),
        ("a: [- b]\n", 1, "'- '"),
        ("a: [&x]\n", 1, "anchors, aliases and tags"),
        ("a: [yes]\n", 1, "boolean or null"),
        ("a: [1.5]\n", 1, "number, date or time"),
        ("a: ['b, c\n", 1, "unclosed quote"),
        # quoted scalars
        ("a: 'b'c\n", 1, "only a comment"),
        ("a: 'b'#c\n", 1, "only a comment"),
        ('a: "b\\n"\n', 1, "only escapes"),
        ('a: "b\\t"\n', 1, "only escapes"),
        ('a: "b\\u0041"\n', 1, "only escapes"),
        ("'a': b\n", 1, "a key must be"),
        ("- 'a': b\n", 1, "only a comment"),
        # keys
        ("A: b\n", 1, "a key must be"),
        ("my-key: b\n", 1, "a key must be"),
        ("a b: c\n", 1, "a key must be"),
        ("a : b\n", 1, "a key must be"),
        ("a" * 65 + ": b\n", 1, "a key must be"),
        ("key:value\n", 1, "space after the ':'"),
        ("a: b\nkey:value\n", 2, "space after the ':'"),
        ("on: b\n", 1, "read this key as a boolean or null"),
        ("a:\n  no: b\n", 2, "read this key as a boolean or null"),
        ("- y: b\n", 1, "read this key as a boolean or null"),
        ("null: b\n", 1, "read this key as a boolean or null"),
        # multi-line scalars
        ("a: b\n  c\n", 2, "multi-line values"),
        ("- b\n  c\n", 2, "multi-line values"),
        ("a:\n  just text\n", 2, "on the same line as its key"),
        ("-\n  just text\n", 2, "on the same line as its key"),
        ("a: b\n  c: d\n", 2, "unexpected indentation"),
        ("- b\n  c: d\n", 2, "unexpected indentation"),
        ("a:\n  [b, c]\n", 2, "on the same line as its key"),
        # a key or '-' with nothing under it
        ("a:\n", 1, "no value and no more-indented block"),
        ("a:\nb: c\n", 1, "no value and no more-indented block"),
        ("a:\n  b:\nc: d\n", 2, "no value and no more-indented block"),
        ("-\n", 1, "no value and no more-indented block"),
        ("- a\n-\n", 2, "no value and no more-indented block"),
        ("a:\n- b\n", 2, "indent the list under its key"),
        ("- a:\n  - b\n", 2, "indent the list under its key"),
        # indentation
        ("  a: 1\nb: 2\n", 2, "bad indentation"),
        ("a:\n    b: 1\n  c: 2\n", 3, "bad indentation"),
        ("- a: 1\n b: 2\n", 2, "bad indentation"),
        ("a: 1\n- b\n", 2, "a list item where a key was expected"),
        ("- b\na: 1\n", 2, "a key where a list item was expected"),
        # the top level
        ("hello\n", 1, "the top level must be a mapping"),
        ("'hello'\n", 1, "the top level must be a mapping"),
        ("[a, b]\n", 1, "the top level must be a mapping"),
        ("# only a comment\nhello\n", 2, "the top level must be a mapping"),
        ("a: b\nhello\n", 2, "expected 'key: value' or '- item'"),
    ],
)
def test_each_refusal_names_its_line(text: str, line: int, needle: str) -> None:
    error = refusal(text)

    assert error.line == line
    assert needle in error.reason, error.reason


@pytest.mark.parametrize("char", list("]{}&*!|>%@`,?:"))
def test_a_plain_scalar_may_not_start_with_an_indicator(char: str) -> None:
    error = refusal(f"a: b\nc: {char}x\n")

    assert error.line == 2
    assert "write it in quotes" in error.reason


def test_a_value_starting_with_a_bracket_is_a_flow_list() -> None:
    error = refusal("a: b\nc: [x\n")

    assert error.line == 2
    assert "unclosed [" in error.reason


@pytest.mark.parametrize("char", list("[]{}"))
def test_a_flow_item_may_not_contain_a_flow_indicator(char: str) -> None:
    error = refusal(f"a: b\nc: [x, y{char}z]\n")

    assert error.line == 2


@pytest.mark.parametrize("text", ["", "\n\n", "   \n", "# just a comment\n", "\ufeff", "# a\n  # b\n"])
def test_an_empty_or_comment_only_file_is_refused_on_line_0(text: str) -> None:
    error = refusal(text)

    assert error.line == 0
    assert "the file is empty" in error.reason


@pytest.mark.parametrize(
    "value",
    [
        # the spec's pattern: numbers, dates, times, versions
        "1.5", "1e5", "1E5", "1_000", "0x1F", "0o17", "0b101", "007", "+1", "+0", "-1.5",
        ".5", "1.", "1.2.3", "2026-11-17", "2026-11-17T09:00:00Z", "12:30", "190:20:30",
        "1:20", "3-4", "1-a", "9a", "-0x1", "0.0.0.0",
        # YAML 1.1 floats the spec's pattern misses
        "+.5", "-.5", "+.5e3",
        # what the YAML 1.1 float pattern [-+]?([0-9][0-9_]*)?\.[0-9.]*([eE][-+][0-9]+)? types
        ".", "-.", "+.", "..", ".e+5", ".E-1",
        # YAML 1.1 timestamps hold spaces
        "2001-12-14 21:59:43.10 -5", "2001-12-14 21:59:43", "2001-12-14 x",
        # infinities and not-a-number
        ".inf", ".Inf", ".INF", "-.inf", "+.inf", ".nan", ".NaN", ".NAN", "-.NaN", ".iNf",
        # YAML 1.1's value and merge keys
        "=", "<<",
    ],
)
def test_a_plain_scalar_another_reader_types_is_refused(value: str) -> None:
    error = refusal(f"a: {value}\n")

    assert error.line == 1
    assert "number, date or time" in error.reason
    assert "write it in quotes" in error.reason
    assert parse(f"a: '{value}'\n") == {"a": value}


@pytest.mark.parametrize(
    "value",
    [
        "y", "Y", "n", "N", "yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON",
        "off", "Off", "OFF", "True", "TRUE", "False", "FALSE", "tRUE", "null", "Null",
        "NULL", "~",
    ],
)
def test_a_boolean_or_null_other_than_true_and_false_is_refused(value: str) -> None:
    error = refusal(f"a: {value}\n")

    assert error.line == 1
    assert "boolean or null" in error.reason
    assert parse(f"a: '{value}'\n") == {"a": value}


def test_an_integer_too_long_to_read_is_refused() -> None:
    error = refusal("a: 1" + "0" * 5000 + "\n")

    assert error.line == 1
    assert "too long" in error.reason


def nested_mappings(levels: int) -> str:
    return "".join(f"{'  ' * level}k{level}:\n" for level in range(levels - 1)) + (
        f"{'  ' * (levels - 1)}last: x\n"
    )


def test_nesting_up_to_32_levels_is_read() -> None:
    value = parse(nested_mappings(MAX_DEPTH))

    depth = 0
    while isinstance(value, dict):
        depth += 1
        value = next(iter(value.values()))
    assert (depth, value) == (MAX_DEPTH, "x")


def test_nesting_deeper_than_32_levels_is_refused() -> None:
    error = refusal(nested_mappings(MAX_DEPTH + 1))

    assert error.line == MAX_DEPTH + 1
    assert "nesting deeper than 32 levels" in error.reason


def test_a_flow_list_counts_as_a_level() -> None:
    at_limit = nested_mappings(MAX_DEPTH - 1).replace("last: x", "last: [x]")
    too_deep = nested_mappings(MAX_DEPTH).replace("last: x", "last: [x]")

    assert parse(at_limit)
    error = refusal(too_deep)
    assert error.line == MAX_DEPTH
    assert "nesting deeper than 32 levels" in error.reason


def test_deep_lists_are_refused_too() -> None:
    text = "".join(f"{'  ' * level}-\n" for level in range(MAX_DEPTH)) + f"{'  ' * MAX_DEPTH}- x\n"

    error = refusal(text)

    assert error.line == MAX_DEPTH + 1
    assert "nesting deeper" in error.reason


def test_the_reason_truncates_long_input() -> None:
    key = "k" + "x" * 62 + "z"  # 64 characters, the longest key
    error = refusal(f"{key}: 1\n{key}: 2\n")

    assert error.line == 2
    assert key not in error.reason
    shown = error.reason.split("'")[1]
    assert shown.endswith("...")
    assert len(shown) <= SHOWN
    assert key.startswith(shown.removesuffix("..."))


@pytest.mark.parametrize(
    "text",
    [
        "a: 'never closed " + "x" * 100 + "\n",
        "a: [" + "item, " * 50 + "\n",
        "a: " + "x" * 100 + ": y\n",
        "A" * 100 + ": b\n",
        "a: b\n" + "  " + "z" * 100 + "\n",
        "a: 1" + "2" * 100 + ".5\n",
        "k" * 64 + ": 1\n" + "k" * 64 + ": 2\n",
    ],
)
def test_no_reason_repeats_more_than_40_characters_of_the_input(text: str) -> None:
    error = refusal(text)

    longest_common = max(
        (
            length
            for start in range(len(error.reason))
            for length in range(1, len(error.reason) - start + 1)
            if error.reason[start : start + length] in text
        ),
        default=0,
    )
    assert longest_common <= SHOWN, error.reason

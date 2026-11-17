"""The Sales Playbook as a snapshot (``nextron.domain.playbook``, Story 4.4): every
``read_playbook`` rule and problem, the BOM and CRLF normalisation, the snapshot's checks, the
frame around a pinned snapshot, the problem texts and the sync triggers."""

import hashlib
from dataclasses import FrozenInstanceError
from typing import Final

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.playbook import (
    AUTHORITY_ORDER,
    MAX_PLAYBOOK_BYTES,
    MAX_PLAYBOOK_CHARS,
    VERSION_PATTERN,
    InvalidPlaybook,
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    playbook_section,
    problem_text,
    read_playbook,
)

BOM: Final = chr(0xFEFF)
TEXT: Final = (
    "---\ntitle: Nextron Sales Playbook\nversion: 0.2.0\nupdated: 2026-11-14\n---\n\n"
    "# Nextron Sales Playbook\n\nPlaybookTextMarker: cite one Signal.\n"
)


def problem_of(data: bytes) -> PlaybookProblem:
    with pytest.raises(InvalidPlaybook) as raised:
        read_playbook(data)
    return raised.value.problem


# read_playbook: the happy path ----------------------------------------------------------------


def test_a_playbook_reads_as_its_version_and_whole_text() -> None:
    snapshot = read_playbook(TEXT.encode("utf-8"))

    assert snapshot == PlaybookSnapshot("0.2.0", TEXT)  # front matter included
    assert snapshot.sha256 == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert snapshot.short == snapshot.sha256[:12]


def test_a_bom_and_crlf_line_ends_hash_the_same_as_the_plain_lf_text() -> None:  # Row: BOM, CRLF
    plain = read_playbook(TEXT.encode("utf-8"))
    windows = read_playbook(b"\xef\xbb\xbf" + TEXT.replace("\n", "\r\n").encode("utf-8"))
    old_mac = read_playbook(TEXT.replace("\n", "\r").encode("utf-8"))

    assert windows == plain == old_mac
    assert windows.sha256 == plain.sha256


def test_only_one_leading_bom_is_dropped() -> None:
    data = (BOM + BOM + TEXT).encode("utf-8")

    assert problem_of(data) is PlaybookProblem.NO_VERSION  # the second hides the fence


@pytest.mark.parametrize(
    ("line", "version"),
    [
        ("version: 0.2.0", "0.2.0"),
        ("version:0.2.0", "0.2.0"),
        ("version:   1.0.0-rc.1+build.5   ", "1.0.0-rc.1+build.5"),
        ('version: "0.3.0"', "0.3.0"),
        ("version: '0.3.0'", "0.3.0"),
        ("version: 0.3.0 # the next release", "0.3.0"),
        ('version: "0.3.0"  # quoted, then a comment', "0.3.0"),
        ("version: 0.3.0\t# a comment after a tab", "0.3.0"),
        ("version: 0.3.0\t\t#tabs, then a comment", "0.3.0"),
        ("version: 7", "7"),
        ("version: " + "a" * 32, "a" * 32),
        ("version: v2", "v2"),
        ("version: 0.3.0\t", "0.3.0"),
    ],
)
def test_the_version_value_is_stripped_cut_and_unquoted(line: str, version: str) -> None:
    text = f"---\ntitle: T\n{line}\n---\nBody.\n"

    assert read_playbook(text.encode("utf-8")).version == version


def test_fences_may_carry_trailing_spaces_and_the_body_may_hold_more_fences() -> None:
    text = "---  \nversion: 1.2.3\n--- \t\nBody.\n---\nversion: 9.9.9\n---\n"

    assert read_playbook(text.encode("utf-8")).version == "1.2.3"


def test_a_nested_or_commented_version_line_isnt_the_top_level_one() -> None:
    text = "---\nmeta:\n  version: 1.0.0\n# version: 2.0.0\nversion: 3.0.0\n---\nBody.\n"

    assert read_playbook(text.encode("utf-8")).version == "3.0.0"


def test_the_largest_playbook_is_accepted() -> None:
    head = "---\nversion: 1.0.0\n---\n"
    text = head + "x" * (MAX_PLAYBOOK_CHARS - len(head))

    assert len(read_playbook(text.encode("utf-8")).content) == MAX_PLAYBOOK_CHARS


# read_playbook: the problems ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "# No front matter at all\n",
        "",
        "\n---\nversion: 1.0.0\n---\n",  # the front matter isn't on the first line
        "---\nversion: 1.0.0\n",  # never closed
        "---\ntitle: T\n---\nversion: 1.0.0\n",  # the version is in the body
        "---\n  version: 1.0.0\n---\nBody.\n",  # indented: not top level
        "---\nversions: 1.0.0\n---\nBody.\n",
        "---\nVersion: 1.0.0\n---\nBody.\n",
        "--- title\nversion: 1.0.0\n---\n",
    ],
)
def test_a_missing_version_is_no_version(text: str) -> None:  # Row: No version
    assert problem_of(text.encode("utf-8")) is PlaybookProblem.NO_VERSION


@pytest.mark.parametrize(
    "line",
    [
        "version:",
        "version:   ",
        'version: ""',
        "version: 1.0 0",
        "version: .1",
        "version: -1",
        "version: 1_0",
        "version: 1/0",
        "version: " + "a" * 33,
        "version: \"0.3.0'",
        "version: caf" + chr(0xE9),
        "version: 0.3.0#no space before the hash",
    ],
)
def test_a_bad_version_value_is_bad_version(line: str) -> None:  # Row: Other problems
    text = f"---\n{line}\n---\nBody.\n"

    assert problem_of(text.encode("utf-8")) is PlaybookProblem.BAD_VERSION


def test_a_repeated_version_line_is_bad_version() -> None:
    text = "---\nversion: 1.0.0\ntitle: T\nversion: 1.0.0\n---\nBody.\n"

    assert problem_of(text.encode("utf-8")) is PlaybookProblem.BAD_VERSION


@pytest.mark.parametrize(
    "data",
    [
        b"---\nversion: 1.0.0\n---\n\xff\xfe\n",
        b"---\nversion: 1.0.0\n---\n\xed\xa0\x80\n",  # an encoded surrogate
        "---\nversion: 1.0.0\n---\nCafé\n".encode("latin-1"),
    ],
)
def test_bytes_that_arent_utf8_are_not_utf8(data: bytes) -> None:
    assert problem_of(data) is PlaybookProblem.NOT_UTF8


def test_too_many_bytes_is_too_large_before_decoding() -> None:
    data = b"\xff" * (MAX_PLAYBOOK_BYTES + 1)  # not UTF-8 either: the size is checked first

    assert problem_of(data) is PlaybookProblem.TOO_LARGE


def test_too_many_characters_is_too_large() -> None:
    head = "---\nversion: 1.0.0\n---\n"
    text = head + "x" * (MAX_PLAYBOOK_CHARS - len(head) + 1)

    assert problem_of(text.encode("utf-8")) is PlaybookProblem.TOO_LARGE


def test_the_character_limit_counts_after_crlf_is_normalised() -> None:
    head = "---\r\nversion: 1.0.0\r\n---\r\n"
    body = "x\r\n" * ((MAX_PLAYBOOK_CHARS - len(head.replace("\r", ""))) // 2)
    text = head + body

    snapshot = read_playbook(text.encode("utf-8"))  # more characters in CRLF, fewer in LF
    assert len(text) > MAX_PLAYBOOK_CHARS >= len(snapshot.content)


def test_read_playbook_takes_bytes() -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        read_playbook(TEXT)  # type: ignore[arg-type]


def test_invalid_playbook_carries_its_problem_and_never_the_text() -> None:
    with pytest.raises(InvalidPlaybook) as raised:
        read_playbook(b"---\ntitle: PlaybookTextMarker\n---\n")

    assert isinstance(raised.value, DomainError)
    assert raised.value.code == "invalid_playbook"
    assert raised.value.problem is PlaybookProblem.NO_VERSION
    assert raised.value.message == problem_text(PlaybookProblem.NO_VERSION)
    assert "PlaybookTextMarker" not in str(raised.value)


# The problem texts ----------------------------------------------------------------------------


def test_the_problems_are_exactly_the_five_codes() -> None:
    assert [problem.value for problem in PlaybookProblem] == [
        "unreadable",
        "too_large",
        "not_utf8",
        "no_version",
        "bad_version",
    ]


@pytest.mark.parametrize("problem", list(PlaybookProblem))
def test_every_problem_text_says_no_snapshot_was_stored(problem: PlaybookProblem) -> None:
    text = problem_text(problem)

    first, _, rest = text.partition(", so no snapshot was stored.")
    assert first and first[0].islower()  # it reads after "Sales Playbook: "
    assert (rest == "") is (problem is not PlaybookProblem.NO_VERSION)


def test_the_no_version_text_says_how_to_fix_it() -> None:
    assert problem_text(PlaybookProblem.NO_VERSION).endswith(
        'Add a line such as "version: 0.3.0" between the two --- lines at the top of the file.'
    )
    assert "100,000 characters" in problem_text(PlaybookProblem.TOO_LARGE)


def test_problem_text_takes_a_problem() -> None:
    with pytest.raises(TypeError):
        problem_text("no_version")  # type: ignore[arg-type]


# PlaybookSnapshot -----------------------------------------------------------------------------


def test_a_snapshot_is_frozen_and_its_repr_hides_the_content() -> None:
    snapshot = PlaybookSnapshot("0.2.0", TEXT)

    with pytest.raises(FrozenInstanceError):
        snapshot.version = "0.3.0"  # type: ignore[misc]
    assert "PlaybookTextMarker" not in repr(snapshot)
    assert "0.2.0" in repr(snapshot)


@pytest.mark.parametrize(
    ("version", "content", "error", "message"),
    [
        (2, TEXT, TypeError, "version must be a str"),
        ("", TEXT, ValueError, "version must match"),
        (".1", TEXT, ValueError, "version must match"),
        ("a" * 33, TEXT, ValueError, "version must match"),
        ("1.0", b"text", TypeError, "content must be a str"),
        ("1.0", " \n\t", ValueError, "content must not be blank"),
        ("1.0", "x" * (MAX_PLAYBOOK_CHARS + 1), ValueError, "at most 100,000"),
        ("1.0", "bad \ud800 surrogate", ValueError, "content must encode as UTF-8"),
    ],
)
def test_a_snapshot_refuses_each_bad_field_by_name(
    version: object, content: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message) as raised:
        PlaybookSnapshot(version, content)  # type: ignore[arg-type]

    assert "surrogate" not in str(raised.value)


def test_the_version_pattern_is_the_specified_one() -> None:
    assert VERSION_PATTERN.pattern == "[0-9A-Za-z][0-9A-Za-z.+-]{0,31}"
    assert MAX_PLAYBOOK_CHARS == 100_000
    assert MAX_PLAYBOOK_BYTES == 400_000


# The frame ------------------------------------------------------------------------------------


def test_the_frame_states_the_order_of_authority_before_the_playbook() -> None:
    snapshot = PlaybookSnapshot("0.2.0", TEXT)

    section = playbook_section(snapshot)

    assert AUTHORITY_ORDER == (
        "Order of authority: law and platform rules come first, then the Step brief, then the "
        "Sales Playbook. Where they disagree, follow the earlier one."
    )
    assert section == f"{AUTHORITY_ORDER}\n\nSales Playbook, version 0.2.0:\n\n{TEXT}"
    assert section.index(AUTHORITY_ORDER) < section.index(TEXT)


def test_playbook_section_takes_a_snapshot() -> None:
    with pytest.raises(TypeError):
        playbook_section(TEXT)  # type: ignore[arg-type]


# SyncTrigger ----------------------------------------------------------------------------------


def test_each_trigger_names_its_actor_and_cause() -> None:
    assert [trigger.value for trigger in SyncTrigger] == ["startup", "operator_check"]
    assert (SyncTrigger.STARTUP.actor, SyncTrigger.STARTUP.cause) == (
        "system:playbook",
        "startup_sync",
    )
    assert (SyncTrigger.OPERATOR_CHECK.actor, SyncTrigger.OPERATOR_CHECK.cause) == (
        "operator",
        "operator_check",
    )

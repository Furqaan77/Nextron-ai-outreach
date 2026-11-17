"""``nextron.domain.prompts`` (Story 2.8): decoding a prompt file, parsing ``manifest.lock``,
``PromptFile`` and ``verify``, and that no message ever holds prompt text."""

import hashlib

import pytest

from nextron.domain.ai import Effort, RouteSpec, RouteTuple, text_sha256
from nextron.domain.errors import DomainError
from nextron.domain.prompts import (
    InvalidPrompts,
    ManifestEntry,
    PromptFile,
    decode_prompt,
    parse_manifest,
    verify,
)

TEXT = "You extract Signals. Pr0mptMarker\nSecond line.\n"
SHA = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
OTHER = "You list people. Pr0mptMarker\n"
OTHER_SHA = text_sha256(OTHER)


def manifest(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# Decoding ---------------------------------------------------------------------------------------


def test_crlf_and_cr_checkouts_hash_the_same_as_lf() -> None:
    lf = TEXT.encode("utf-8")
    crlf = TEXT.replace("\n", "\r\n").encode("utf-8")
    cr = TEXT.replace("\n", "\r").encode("utf-8")

    texts = {decode_prompt(data, "extract_signals/v1.md") for data in (lf, crlf, cr)}

    assert texts == {TEXT}
    assert text_sha256(TEXT) == SHA


def test_bytes_that_arent_utf8_are_refused_by_name() -> None:
    with pytest.raises(InvalidPrompts) as raised:
        decode_prompt(b"caf\xe9 Pr0mptMarker", "extract_people/v1.md")

    assert raised.value.message == "extract_people/v1.md: the file isn't valid UTF-8"
    assert raised.value.code == "invalid_prompts"
    assert isinstance(raised.value, DomainError)


def test_a_byte_order_mark_is_kept_so_it_changes_the_hash() -> None:
    text = decode_prompt(b"\xef\xbb\xbf" + TEXT.encode("utf-8"), "x")

    assert text == "﻿" + TEXT
    assert text_sha256(text) != SHA


def test_decode_takes_bytes_only() -> None:
    with pytest.raises(TypeError):
        decode_prompt(TEXT, "x")  # type: ignore[arg-type]


# The manifest -----------------------------------------------------------------------------------


def test_a_manifest_parses_into_entries_in_file_order() -> None:
    text = manifest(
        "# a comment",
        "",
        f"extract_signals v1 {SHA}",
        "#",
        f"extract_people v12 {OTHER_SHA}",
        "   ",
    )

    assert parse_manifest(text) == (
        ManifestEntry(3, "extract_signals", "v1", SHA),
        ManifestEntry(5, "extract_people", "v12", OTHER_SHA),
    )
    assert parse_manifest("") == ()
    assert parse_manifest(text.replace("\n", "\r\n")) == parse_manifest(text)


def test_a_manifest_saved_with_a_byte_order_mark_parses_the_same() -> None:
    text = manifest(f"extract_signals v1 {SHA}", f"extract_people v1 {OTHER_SHA}")
    saved = b"\xef\xbb\xbf" + text.encode("utf-8")

    assert parse_manifest(decode_prompt(saved, "manifest.lock")) == parse_manifest(text)
    assert parse_manifest("﻿# comment\n" + text)[0].line == 2


@pytest.mark.parametrize(
    "line",
    [
        f"extract_signals v1 {SHA.upper()}",
        f"extract_signals  v1 {SHA}",
        f"extract_signals\tv1 {SHA}",
        f" extract_signals v1 {SHA}",
        f"extract_signals v1 {SHA} ",
        f"extract_signals v1 {SHA[:-1]}",
        f"extract_signals v1 {SHA}0",
        "extract_signals v1",
        f"extract_signals {SHA}",
        f"Extract_Signals v1 {SHA}",
        f"extract-signals v1 {SHA}",
        f"extract_signals v0 {SHA}",
        f"extract_signals v01 {SHA}",
        f"extract_signals v10000 {SHA}",
        f"extract_signals 1 {SHA}",
        f"extract_signals v1 {SHA} extra",
        f"  # extract_signals v1 {SHA}",
    ],
)
def test_a_malformed_line_is_refused_by_its_number(line: str) -> None:
    with pytest.raises(InvalidPrompts) as raised:
        parse_manifest(manifest("# header", line))

    assert raised.value.message.startswith("manifest.lock line 2: expected")


def test_a_repeated_route_and_version_is_refused() -> None:
    text = manifest(f"extract_signals v1 {SHA}", "", f"extract_signals v1 {OTHER_SHA}")

    with pytest.raises(InvalidPrompts) as raised:
        parse_manifest(text)

    assert raised.value.message == (
        "manifest.lock line 3: extract_signals v1 is already listed on line 1"
    )


# PromptFile -------------------------------------------------------------------------------------


def test_a_prompt_file_hashes_like_a_route_tuple_and_hides_its_text() -> None:
    prompt = PromptFile("extract_signals", "v1", TEXT)
    route_tuple = RouteTuple("extract_signals", "claude-haiku-5-5", Effort.MEDIUM, "v1", 1, TEXT)

    assert prompt.sha256 == route_tuple.prompt_sha256 == SHA
    assert prompt.file_name == "extract_signals/v1.md"
    assert "Pr0mptMarker" not in repr(prompt)
    assert repr(prompt) == "PromptFile(route='extract_signals', version='v1')"


@pytest.mark.parametrize(
    ("route", "version", "text", "error", "field"),
    [
        (7, "v1", TEXT, TypeError, "route"),
        ("Extract", "v1", TEXT, ValueError, "route"),
        ("extract_signals", "v0", TEXT, ValueError, "version"),
        ("extract_signals", 1, TEXT, TypeError, "version"),
        ("extract_signals", "v1", b"bytes", TypeError, "text"),
        ("extract_signals", "v1", " \n\t", ValueError, "text"),
        ("extract_signals", "v1", "", ValueError, "text"),
        ("extract_signals", "v1", "lone \ud800 surrogate", ValueError, "text"),
    ],
)
def test_a_prompt_file_checks_its_fields(
    route: object, version: object, text: object, error: type[Exception], field: str
) -> None:
    with pytest.raises(error, match=f"^{field} "):
        PromptFile(route, version, text)  # type: ignore[arg-type]


# verify -----------------------------------------------------------------------------------------

ENTRIES = (
    ManifestEntry(3, "extract_signals", "v1", SHA),
    ManifestEntry(4, "extract_people", "v1", OTHER_SHA),
)
FILES = {"extract_people/v1.md": OTHER, "extract_signals/v1.md": TEXT}


def test_verify_returns_the_files_in_manifest_order() -> None:
    found = verify(ENTRIES, FILES)

    assert found == (
        PromptFile("extract_signals", "v1", TEXT),
        PromptFile("extract_people", "v1", OTHER),
    )


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (
            {**FILES, "extract_signals/v01.md": TEXT},
            "extract_signals/v01.md: a prompt file must be named <route>/v<n>.md",
        ),
        (
            {**FILES, "extract_signals/draft.md": TEXT},
            "extract_signals/draft.md: a prompt file must be named <route>/v<n>.md",
        ),
        (
            {**FILES, "Extract/v1.md": TEXT},
            "Extract/v1.md: a prompt file must be named <route>/v<n>.md",
        ),
        (
            {"extract_signals/v1.md": TEXT},
            "manifest.lock line 4: extract_people v1: the file extract_people/v1.md is missing",
        ),
        (
            {**FILES, "extract_people/v1.md": " \n"},
            "manifest.lock line 4: extract_people v1: the file extract_people/v1.md is blank",
        ),
        (
            {**FILES, "extract_signals/v1.md": TEXT + "edited"},
            "manifest.lock line 3: extract_signals v1: the SHA-256 of extract_signals/v1.md "
            "doesn't match; a used prompt never changes: add a new version",
        ),
        (
            {**FILES, "extract_signals/v2.md": "a new version nobody listed"},
            "extract_signals/v2.md: the file isn't listed in manifest.lock",
        ),
        (
            {**FILES, "signal_inbox_parse/v1.md": "an unlisted route"},
            "signal_inbox_parse/v1.md: the file isn't listed in manifest.lock",
        ),
    ],
)
def test_verify_refuses_each_disagreement(files: dict[str, str], message: str) -> None:
    with pytest.raises(InvalidPrompts) as raised:
        verify(ENTRIES, files)

    assert raised.value.message.startswith(message)
    assert "Pr0mptMarker" not in raised.value.message


def test_verify_refuses_entries_that_repeat_a_version() -> None:
    entries = (*ENTRIES, ManifestEntry(9, "extract_signals", "v1", SHA))

    with pytest.raises(InvalidPrompts, match="lists extract_signals v1 more than once"):
        verify(entries, FILES)


def test_verify_checks_its_argument_types() -> None:
    with pytest.raises(TypeError):
        verify([("extract_signals", "v1", SHA)], FILES)  # type: ignore[list-item]
    with pytest.raises(TypeError):
        verify(ENTRIES, {"extract_signals/v1.md": b"bytes"})  # type: ignore[dict-item]


# RouteSpec and the actor ------------------------------------------------------------------------


HAIKU = "claude-haiku-5-5"


def test_a_route_spec_checks_the_route_fields() -> None:
    spec = RouteSpec("extract_signals", HAIKU, Effort.MEDIUM, "v1", 1)

    assert (spec.route, spec.prompt_version, spec.schema_version) == ("extract_signals", "v1", 1)
    with pytest.raises(TypeError, match="effort"):
        RouteSpec("extract_signals", HAIKU, "medium", "v1", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="prompt_version"):
        RouteSpec("extract_signals", HAIKU, Effort.MEDIUM, "v0", 1)
    with pytest.raises(TypeError, match="schema_version"):
        RouteSpec("extract_signals", HAIKU, Effort.MEDIUM, "v1", True)
    with pytest.raises(ValueError, match="route"):
        RouteSpec("Extract", HAIKU, Effort.MEDIUM, "v1", 1)


@pytest.mark.parametrize("route", ["extract_signals", "a", "r" * 64])
@pytest.mark.parametrize("version", ["v1", "v9999"])
def test_a_tuples_actor_is_an_event_log_actor(route: str, version: str) -> None:
    from nextron.db.eventlog import ACTOR_PATTERN  # noqa: PLC0415 - a db module, tests only

    route_tuple = RouteTuple(route, HAIKU, Effort.MEDIUM, version, 1, TEXT)

    assert route_tuple.actor == f"ai:{route}@{version}"
    assert ACTOR_PATTERN.fullmatch(route_tuple.actor)

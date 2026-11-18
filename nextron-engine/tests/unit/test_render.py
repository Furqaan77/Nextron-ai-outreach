"""``nextron.domain.render``'s ``compose``, ``canon`` and ``text_hash`` (FR-20, AD-6; Story 4.12):
golden hashes, the canonical forms that share a hash, the changes that don't, and a composed
message that is plain text with the full footer even for an empty body."""

import hashlib
import re
from dataclasses import dataclass

import pytest

from nextron.domain import render
from nextron.domain.render import Composed, canon, compose, text_hash

TO = "ann@firm.example"
INBOX = "01JD3QZ8X5V9K2M7N4P6R8T0AA"
OTHER_INBOX = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
SUBJECT = "A question about intake"
FOOTER = (
    "Furqaan Ahmed\nNextron AI Ltd\n1 High Street, London\n"
    "Not relevant? Reply 'no' and I won't contact you again."
)
BODY = "Hi Ann,\n\nA short note.\n\n" + FOOTER
GOLDEN = "724d6bcd7d640ab2aec7801f7a4859f850f87ab55099a3c2fe92ec2cb2947052"


@dataclass(frozen=True)
class Revision:
    subject: str
    body: str


@dataclass(frozen=True)
class Snapshot:
    text: str


def test_the_golden_hash_is_sha256_of_the_spines_layout() -> None:
    raw = f"{TO}\n{INBOX}\n{SUBJECT}\n\n{BODY}".encode()

    assert text_hash(TO, INBOX, SUBJECT, BODY) == hashlib.sha256(raw).hexdigest()
    assert text_hash(TO, INBOX, SUBJECT, BODY) == GOLDEN
    assert re.fullmatch(r"[0-9a-f]{64}", GOLDEN)


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        (SUBJECT, BODY.replace("\n", "\r\n")),  # CRLF
        (SUBJECT, BODY.replace("\n", "\r")),  # CR
        (SUBJECT + "  ", BODY.replace("A short note.", "A short note.   \t")),  # trailing spaces
        (SUBJECT, BODY + "\n\n  \n"),  # trailing blank lines
    ],
)
def test_line_breaks_and_trailing_whitespace_give_the_same_hash(subject: str, body: str) -> None:
    assert text_hash(TO, INBOX, subject, body) == GOLDEN


def test_nfd_and_nfc_give_the_same_hash() -> None:
    nfc = "Hi Zoë, café"
    nfd = "Hi Zoë, café"
    assert nfc != nfd

    assert text_hash(TO, INBOX, nfd, nfd) == text_hash(TO, INBOX, nfc, nfc)
    assert canon(nfd) == nfc


@pytest.mark.parametrize(
    ("to", "inbox", "subject", "body"),
    [
        ("bob@firm.example", INBOX, SUBJECT, BODY),
        (TO, OTHER_INBOX, SUBJECT, BODY),  # another inbox
        (TO, INBOX, SUBJECT + "!", BODY),
        (TO, INBOX, SUBJECT, BODY.replace("London", "Leeds")),  # another footer
        (TO, INBOX, SUBJECT, " " + BODY),  # leading whitespace is kept
    ],
)
def test_any_other_change_gives_another_hash(to: str, inbox: str, subject: str, body: str) -> None:
    assert text_hash(to, inbox, subject, body) != GOLDEN


def test_canon_normalises_and_strips() -> None:
    assert canon("a \r\nb\t\r\rc\n\n \n") == "a\nb\n\nc"
    assert canon("") == ""
    assert canon("\n\n") == ""
    assert canon("  lead") == "  lead"


def test_compose_appends_a_blank_line_and_the_footer() -> None:
    composed = compose(Revision(SUBJECT, "Hi Ann,\n\nA short note."), Snapshot(FOOTER), TO)

    assert composed == Composed(TO, SUBJECT, BODY)
    assert text_hash(composed.to_address, INBOX, composed.subject, composed.body) == GOLDEN


def test_an_empty_body_still_ends_with_the_full_footer() -> None:
    composed = compose(Revision(SUBJECT, ""), Snapshot(FOOTER), TO)

    assert composed.body == "\n\n" + FOOTER
    assert composed.body.endswith(FOOTER)


def test_another_footer_gives_another_hash() -> None:
    first = compose(Revision(SUBJECT, "Hi"), Snapshot(FOOTER), TO)
    second = compose(Revision(SUBJECT, "Hi"), Snapshot(FOOTER + "\nUS: solicitation"), TO)

    assert text_hash(TO, INBOX, first.subject, first.body) != text_hash(
        TO, INBOX, second.subject, second.body
    )


def test_compose_is_plain_text_and_adds_nothing() -> None:
    composed = compose(Revision(SUBJECT, "Hi Ann,\nThanks."), Snapshot(FOOTER), TO)

    for text in (composed.subject, composed.body):
        assert "<" not in text and ">" not in text  # no tag, no pixel
        assert "http" not in text  # no link but the footer's (this footer has none)
    assert composed.body == "Hi Ann,\nThanks." + render.BODY_FOOTER_SEPARATOR + FOOTER


def test_compose_keeps_the_footers_own_link_and_nothing_else() -> None:
    footer = FOOTER + "\nYour details came from your firm's website. Privacy notice: https://n.example/p"

    composed = compose(Revision(SUBJECT, "Hi"), Snapshot(footer), TO)

    assert re.findall(r"https?://\S+", composed.body) == ["https://n.example/p"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: compose(Revision(1, "b"), Snapshot("f"), TO),  # type: ignore[arg-type]
        lambda: compose(Revision("s", None), Snapshot("f"), TO),  # type: ignore[arg-type]
        lambda: compose(Revision("s", "b"), Snapshot(b"f"), TO),  # type: ignore[arg-type]
        lambda: compose(Revision("s", "b"), Snapshot("f"), None),  # type: ignore[arg-type]
        lambda: text_hash(None, INBOX, "s", "b"),  # type: ignore[arg-type]
        lambda: text_hash(TO, 7, "s", "b"),  # type: ignore[arg-type]
        lambda: text_hash(TO, INBOX, b"s", "b"),  # type: ignore[arg-type]
        lambda: canon(None),  # type: ignore[arg-type]
    ],
)
def test_a_value_that_isnt_a_str_raises_type_error(call: object) -> None:
    with pytest.raises(TypeError):
        call()  # type: ignore[operator]

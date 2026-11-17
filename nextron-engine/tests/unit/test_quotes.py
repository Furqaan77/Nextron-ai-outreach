"""``nextron.domain.quotes`` (AD-18, Story 2.8): the normalisation and the substring check a
proposed quote must pass. Row: Quote."""

import unicodedata

import pytest

from nextron.domain.quotes import normalise, quote_in

PAGE = "About us … Smith & Jones LLP has 12 lawyers in Leeds. …"


# Row: Quote
@pytest.mark.parametrize(
    ("quote", "text", "found"),
    [
        ("Smith  &\nJones", PAGE, True),
        ("smith & jones", PAGE, False),  # another case
        ("", PAGE, False),
        (" \n\t", PAGE, False),  # only whitespace normalises to nothing
        ("Smith & Jones LLP has 12 lawyers", PAGE, True),
        ("Smith & Jones LLP had 12 lawyers", PAGE, False),
        ("Smith and Jones", PAGE, False),  # nothing but whitespace is forgiven
        ("12 lawyers in Leeds.", PAGE, True),
        ("12 lawyers in Leeds..", PAGE, False),
    ],
)
def test_quote_in(quote: str, text: str, found: bool) -> None:
    assert quote_in(quote, text) is found


def test_unicode_whitespace_and_line_breaks_count_as_one_space() -> None:
    text = "Managing Partner Ann　Lee\r\nof the firm\tand\x0bco"

    assert normalise(text) == "Managing Partner Ann Lee of the firm and co"
    assert quote_in("Managing Partner Ann Lee of the firm", text)


def test_nfc_makes_composed_and_decomposed_letters_equal() -> None:
    composed = "Café Müller"
    decomposed = unicodedata.normalize("NFD", composed)

    assert composed != decomposed
    assert quote_in(decomposed, f"Visit {composed} today")
    assert quote_in(composed, f"Visit {decomposed} today")
    assert normalise(decomposed) == composed


def test_normalise_strips_and_keeps_everything_else() -> None:
    assert normalise("  “Quoted” — text’s  end \n") == "“Quoted” — text’s end"


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        (None, PAGE),
        ("Smith", None),
        (b"Smith", PAGE),
        ("", None),  # an empty quote still checks the text's type
        (" \n", b"bytes"),
    ],
)
def test_both_must_be_text(quote: object, text: object) -> None:
    with pytest.raises(TypeError):
        quote_in(quote, text)  # type: ignore[arg-type]


def test_normalise_takes_text_only() -> None:
    with pytest.raises(TypeError):
        normalise(7)  # type: ignore[arg-type]

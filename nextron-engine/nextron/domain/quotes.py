"""The quote check (AD-18, Story 2.8): is a quote really in the text it cites? Standard library
only.

A model's quote is a proposal. It counts only when it is an exact substring of the stored text it
cites, after both are normalised the same way:

- ``normalise(text)``: Unicode NFC, then each run of Unicode whitespace (every character
  ``str.isspace`` accepts: spaces, tabs, line breaks, no-break and ideographic spaces, and so on)
  becomes one space, then leading and trailing whitespace is stripped.
- ``quote_in(quote, text)``: true when ``normalise(quote)`` is non-empty and a case-sensitive
  substring of ``normalise(text)``.

Nothing else is forgiven: case, punctuation, quotation marks and dashes must match. Story 2.9
adds the 300-character limit when it stores Evidence. Both raise ``TypeError`` unless given
``str`` values.
"""

import re
import unicodedata
from typing import Final

WHITESPACE: Final = re.compile(r"\s+")  # Unicode whitespace in a str pattern


def normalise(text: str) -> str:
    """``text`` in NFC, with each run of whitespace as one space, stripped."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def quote_in(quote: str, text: str) -> bool:
    """Whether the normalised ``quote`` is non-empty and in the normalised ``text``."""
    if not isinstance(quote, str):
        raise TypeError("quote must be a str")
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    wanted = normalise(quote)
    return bool(wanted) and wanted in normalise(text)

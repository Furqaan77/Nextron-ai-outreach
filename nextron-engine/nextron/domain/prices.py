"""Whether a text names a price (FR-23, NFR-7; Stories 4.2 and 4.11): the single definition.

A Campaign's angle note (``nextron.domain.campaign``) and every footer text but the privacy
notice URL (``nextron.domain.settings_schema``'s ``no_price`` rule) are refused when they name a
price. The
check lives in this leaf module, which imports only the standard library, so the settings schema
can use it without importing ``campaign`` (which imports ``markets``, which imports the schema).

- ``CURRENCY_SIGNS``: ``$``, ``£`` and ``€``;
- ``CURRENCY_WORDS``: ``usd``, ``gbp`` and ``eur``;
- ``names_price(text)``: true when ``text`` holds a currency sign, or one of the currency words
  as a word (lowercased, every run of characters other than ``a-z`` and ``0-9`` a word break).
"""

import re
from typing import Final

CURRENCY_SIGNS: Final = frozenset("$£€")
CURRENCY_WORDS: Final = frozenset({"usd", "gbp", "eur"})
_NOT_WORD: Final = re.compile(r"[^a-z0-9]+")


def names_price(note: str) -> bool:
    """Whether ``note`` names a price: a currency sign (``$``, ``£``, ``€``), or the word
    ``usd``, ``gbp`` or ``eur`` (lowercased, every run of other characters a word break)."""
    if any(sign in note for sign in CURRENCY_SIGNS):
        return True
    return not CURRENCY_WORDS.isdisjoint(_NOT_WORD.sub(" ", note.lower()).split())

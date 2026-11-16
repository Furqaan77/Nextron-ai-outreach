"""The shape of an ID (the *IDs* convention): a ULID in canonical text.

Every primary key is a ULID made by python-ulid in the database layer. The domain only checks the
shape, with the standard library: 26 characters of uppercase Crockford base32 (no ``I``, ``L``,
``O`` or ``U``), the first one ``0`` to ``7`` so the value fits in 128 bits. This is the text
``str(ULID())`` produces; lowercase or padded text does not count.
"""

import re
from typing import Final

ULID_PATTERN: Final = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")  # matched with fullmatch


def is_ulid(value: object) -> bool:
    """Whether ``value`` is a ULID string in canonical form."""
    return isinstance(value, str) and ULID_PATTERN.fullmatch(value) is not None

"""The model base and column types (AD-2, AD-3, the *IDs* and *Dates and times* conventions).

- ``Base`` carries ``NAMING_CONVENTION``, so every index and constraint has a stable name that a
  hand-written migration can repeat.
- Each table's primary key is exactly one ``id`` column of type ``Ulid``: declare it with
  ``id: Mapped[str] = ulid_pk()``. ``new_id()`` makes a fresh ULID string.
- Natural keys (place_id, Companies House number, email) are unique columns, never primary keys.
- Datetimes use ``UtcDateTime``; ``Mapped[datetime]`` maps to it, and no column uses
  SQLAlchemy's ``DateTime``.

``tests/contracts/test_persistence_contracts.py`` checks the key and datetime rules on the whole
metadata.
"""

import re
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Dialect, MetaData, String
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column
from sqlalchemy.types import TypeDecorator
from ulid import ULID

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
ULID_LENGTH: Final = 26
UTC_TEXT_LENGTH: Final = 27  # YYYY-MM-DDTHH:MM:SS.ffffffZ
UTC_TEXT_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


def new_id() -> str:
    """A new ULID in canonical text."""
    return str(ULID())


def ulid_text(value: object) -> str:
    """``value`` (a ``ULID`` or a ULID string in either case) as canonical ULID text.

    Raises ``TypeError`` for any other type and ``ValueError`` for a string that is not a ULID.
    The error never repeats the value.
    """
    if isinstance(value, ULID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(ULID.from_str(value))
        except ValueError:
            raise ValueError(
                f"not a ULID: expected {ULID_LENGTH} characters of Crockford base32 "
                f"starting 0-7, got {len(value)} characters"
            ) from None
    raise TypeError(f"a ULID column takes a ULID or a str, not {type(value).__name__}")


def utc_text(value: object) -> str:
    """An aware ``datetime`` as fixed-width UTC text, ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.

    Raises ``TypeError`` for anything that is not a ``datetime`` (a ``date`` included) and
    ``ValueError`` for a naive one.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"a UtcDateTime column takes a datetime, not {type(value).__name__}")
    if value.utcoffset() is None:
        raise ValueError("a UtcDateTime column takes an aware datetime, not a naive one")
    naive_utc = value.astimezone(UTC).replace(tzinfo=None)
    return naive_utc.isoformat(timespec="microseconds") + "Z"


def parse_utc_text(text: str) -> datetime:
    """The aware UTC ``datetime`` that ``utc_text`` stored as ``text``."""
    if not UTC_TEXT_PATTERN.fullmatch(text):
        raise ValueError("stored datetime is not YYYY-MM-DDTHH:MM:SS.ffffffZ text")
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)


class Ulid(TypeDecorator[str]):
    """A ULID stored as python-ulid's canonical text; reads back as ``str``."""

    impl = String(ULID_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else ulid_text(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return value


class UtcDateTime(TypeDecorator[datetime]):
    """An aware datetime stored as fixed-width UTC text with ``Z``; reads back aware UTC."""

    impl = String(UTC_TEXT_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else utc_text(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else parse_utc_text(value)


class Base(DeclarativeBase):
    """Base of every model. Import new model modules in ``nextron/db/models/__init__.py``."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UtcDateTime}


def ulid_pk() -> MappedColumn[Any]:
    """The ``id`` primary key column: a ``Ulid`` that defaults to ``new_id()``."""
    return mapped_column(Ulid, primary_key=True, default=new_id)

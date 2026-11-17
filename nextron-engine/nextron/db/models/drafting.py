"""The ``drafting`` service's tables (AD-3, AD-7).

``footer_snapshots`` (FR-23, Story 4.11; migration ``0013_4_11_footer_snapshots``) holds each
distinct footer text a Market's emails were composed with, written only by
``nextron.db.repos.drafting.footer_snapshots``: a ULID ``id``, the ``market_id`` (a foreign key
to ``markets.id``, no cascade), the ``sha256`` of the text (``nextron.domain.render.
footer_sha256``; ``ck_footer_snapshots_sha256``: 64 characters of ``0-9a-f``), the ``text``
(``ck_footer_snapshots_text``: not empty) and ``created_at``. Every column is NOT NULL, and a
(``market_id``, ``sha256``) pair is stored once (``uq_footer_snapshots_market_id_sha256``), so
identical content reuses its row. The repository has no update or delete, so a snapshot never
changes; there is no trigger, because erasure must be able to redact ``text``.

``text`` holds the sender's name and Nextron's postal address, like ``settings.value``, so it is
classed ``redact``; every other column holds no personal data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

SHA256_LENGTH: Final = 64  # lowercase hex SHA-256
SHA256_CHECK: Final = "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'"
TEXT_CHECK: Final = "length(text) > 0"


class FooterSnapshotRecord(Base):
    """One distinct footer text of one Market."""

    __tablename__ = "footer_snapshots"
    __table_args__ = (
        UniqueConstraint("market_id", "sha256"),
        CheckConstraint(SHA256_CHECK, name="sha256"),
        CheckConstraint(TEXT_CHECK, name="text"),
    )

    id: Mapped[str] = ulid_pk()
    market_id: Mapped[str] = mapped_column(Ulid, ForeignKey("markets.id"))
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)

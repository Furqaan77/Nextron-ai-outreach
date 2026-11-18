"""Story 2.14: each address's checks and verification (FR-15, FR-59, FR-57, AD-4, AD-9), owned by
``research``.

Adds to ``contacts`` four nullable columns, codes or times only, with ``batch_alter_table`` and
``add_column`` only (SQLite adds them in place, without a rebuild) and no CHECK (as Story 2.13's
``email_search_*``): ``email_check_result`` (the last check's result: ``checks_passed``,
``invalid``, ``suppressed``, ``quota_reached`` or the verifier's normalised status),
``email_checked_at`` (when it was recorded), ``email_invalid_reason`` (``syntax``,
``disposable``, ``no_mx``, ``null_mx`` or ``verifier_invalid``, set with an ``invalid`` class)
and ``email_verifier_at`` (when Hunter's Email Verifier answered for the address, at most once
per address). A new address clears all four.

Drops and creates again ``trg_contacts_tombstone`` (0032, Story 3.13; last 0040), the same except
that the four new columns join the columns an erased Contact keeps fixed (22).

Revision ID: 0043_2_14_email_checks
Revises: 0042_4_9_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_2_14_email_checks"
down_revision: str | None = "0042_4_9_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REDACTED = "[erased]"  # nextron.domain.erasure.REDACTED
TOMBSTONE = "an erased Contact is a tombstone"  # 0032's message
# contacts at this revision, less lead_id, is_primary and updated_at (a merge moves a Contact)
CONTACT_FIXED = (
    "id",
    "name",
    "role",
    "email",
    "address_class",
    "linkedin_url",
    "tz",
    "market_id",
    "market_basis",
    "tz_basis",
    "office_country",
    "office_region",
    "created_at",
    "primary_set_by",
    "erased_at",
    "email_search_tried",  # Story 2.13
    "email_search_outcome",
    "email_search_at",
    "email_check_result",  # Story 2.14
    "email_checked_at",
    "email_invalid_reason",
    "email_verifier_at",
)


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("email_check_result", sa.String(32), nullable=True))
        batch.add_column(sa.Column("email_checked_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("email_invalid_reason", sa.String(32), nullable=True))
        batch.add_column(sa.Column("email_verifier_at", sa.String(27), nullable=True))

    # 0040's trigger, with the four new columns fixed on an erased Contact
    tombstone = (
        f"(NEW.name IS NULL OR NEW.name IS '{REDACTED}') "
        f"AND (NEW.role IS NULL OR NEW.role IS '{REDACTED}') "
        "AND NEW.linkedin_url IS NULL AND NEW.office_country IS NULL "
        "AND NEW.office_region IS NULL "
        "AND (NEW.email IS NULL "
        "OR (length(NEW.email) = 64 AND NEW.email NOT GLOB '*[^0-9a-f]*'))"
    )
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in CONTACT_FIXED)
    op.execute("DROP TRIGGER trg_contacts_tombstone")
    op.execute(
        "CREATE TRIGGER trg_contacts_tombstone BEFORE UPDATE ON contacts "
        f"WHEN (OLD.erased_at IS NULL AND NEW.erased_at IS NOT NULL AND NOT ({tombstone})) "
        f"OR (OLD.erased_at IS NOT NULL AND ({changed})) "
        f"BEGIN SELECT RAISE(ABORT, '{TOMBSTONE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

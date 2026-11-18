"""Story 2.10: who chose a Lead's Primary Contact, one Primary per Lead, and the Lead's
"no decision-maker" flag (FR-10, FR-13, FR-51, AD-7, AD-17), all owned by ``research``.

Adds to ``contacts`` the nullable ``primary_set_by`` (8 characters: ``system`` or ``operator``;
the code keeps it set exactly when ``is_primary`` is true) and to ``leads`` (the profile group)
the nullable ``no_decision_maker`` (a Boolean, NULL until the Primary Contact's selection first
runs). Both are added with ``batch_alter_table`` and ``add_column`` only: SQLite adds them in
place, without a rebuild, and no CHECK constrains them (a later rebuild may add one).

Creates the partial unique index ``uq_contacts_one_primary`` on ``contacts (lead_id)`` where
``is_primary = 1``: at most one Primary Contact per Lead.

Revision ID: 0023_2_10_primary_contact
Revises: 0022_2_9_signals_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_2_10_primary_contact"
down_revision: str | None = "0022_2_9_signals_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("primary_set_by", sa.String(8), nullable=True))
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("no_decision_maker", sa.Boolean(), nullable=True))
    op.create_index(
        "uq_contacts_one_primary",
        "contacts",
        ["lead_id"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

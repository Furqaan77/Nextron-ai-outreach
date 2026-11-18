"""Story 3.15: the ``data_subject_requests`` table (FR-52, AD-4, AD-17), owned by ``compliance``,
to Story 3.13's schema.

Creates ``data_subject_requests``, one row per data subject request: a ULID ``id``;
``contact_id`` (``fk_data_subject_requests_contact_id_contacts``, no cascade, indexed by
``ix_data_subject_requests_contact_id``); ``kind`` (``ck_data_subject_requests_kind``: ``erase``
or ``export``); ``status`` (``ck_data_subject_requests_status``: ``open`` or ``completed``);
``request_ref``, the Operator's reference for the request (``ck_data_subject_requests_request_ref``:
1 to 200 characters); ``opened_at``; ``completed_at``, NULL exactly while the request is
``open`` (``ck_data_subject_requests_completed``); and ``receipt_id``, a nullable ULID with no
foreign key until ``erasure_receipts`` exists (Story 3.14).

Revision ID: 0028_3_15_subject_requests
Revises: 0027_2_6_contact_methods
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_3_15_subject_requests"
down_revision: str | None = "0027_2_6_contact_methods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_ref", sa.String(200), nullable=False),
        sa.Column("opened_at", sa.String(27), nullable=False),
        sa.Column("completed_at", sa.String(27), nullable=True),
        sa.Column("receipt_id", sa.String(26), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_data_subject_requests"),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name="fk_data_subject_requests_contact_id_contacts",
        ),
        sa.CheckConstraint(
            "kind IN ('erase','export')", name=op.f("ck_data_subject_requests_kind")
        ),
        sa.CheckConstraint(
            "status IN ('open','completed')", name=op.f("ck_data_subject_requests_status")
        ),
        sa.CheckConstraint(
            "length(request_ref) BETWEEN 1 AND 200",
            name=op.f("ck_data_subject_requests_request_ref"),
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_data_subject_requests_completed"),
        ),
    )
    op.create_index(
        "ix_data_subject_requests_contact_id", "data_subject_requests", ["contact_id"]
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

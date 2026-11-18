"""Story 2.11: each Lead's job postings and the counts of each check (FR-7, FR-11, FR-49, AD-7,
AD-9, INT-10, NFR-6), all owned by ``research``.

Creates ``job_postings``, one posting found on a Lead's careers page or ATS board: a ULID ``id``;
``lead_id`` (``fk_job_postings_lead_id_leads``, indexed by ``ix_job_postings_lead_id``);
``source`` (``ck_job_postings_source``: ``careers_page``, ``greenhouse``, ``lever`` or
``ashby``) and the nullable ``board_token`` (100), NULL exactly for a ``careers_page`` posting
(``ck_job_postings_board_token``); ``url`` (2,048), ``title`` (300) and the nullable
``description`` (300); the nullable ``posted_date`` (a date); ``first_seen_at`` and
``last_checked_at``; ``status`` (``ck_job_postings_status``: ``live`` or ``closed``) and the
nullable ``closed_at``, set exactly when the posting is closed (``ck_job_postings_closed_at``);
the nullable ``signal_id`` (``fk_job_postings_signal_id_signals``, indexed by
``ix_job_postings_signal_id``) and ``evidence_id`` (``fk_job_postings_evidence_id_evidence``).
Unique on ``(lead_id, url, title)`` (``uq_job_postings_lead_id_url_title``). No foreign key
cascades.

Adds to ``crawl_runs`` the nullable Integers ``careers_postings``, ``careers_events``,
``greenhouse_postings``, ``greenhouse_events``, ``lever_postings``, ``lever_events``,
``ashby_postings``, ``ashby_events`` and ``board_errors``, with ``batch_alter_table`` and
``add_column`` only: SQLite adds them in place, without a rebuild, and no CHECK constrains them
(NULL means the run didn't check that source).

Revision ID: 0035_2_11_job_postings
Revises: 0034_4_6_ai_test_sets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_2_11_job_postings"
down_revision: str | None = "0034_4_6_ai_test_sets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTS = (
    "careers_postings",
    "careers_events",
    "greenhouse_postings",
    "greenhouse_events",
    "lever_postings",
    "lever_events",
    "ashby_postings",
    "ashby_events",
    "board_errors",
)


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("board_token", sa.String(100), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=True),
        sa.Column("first_seen_at", sa.String(27), nullable=False),
        sa.Column("last_checked_at", sa.String(27), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("closed_at", sa.String(27), nullable=True),
        sa.Column("signal_id", sa.String(26), nullable=True),
        sa.Column("evidence_id", sa.String(26), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_job_postings"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_job_postings_lead_id_leads"),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name="fk_job_postings_signal_id_signals"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_job_postings_evidence_id_evidence"
        ),
        sa.UniqueConstraint("lead_id", "url", "title", name="uq_job_postings_lead_id_url_title"),
        sa.CheckConstraint(
            "source IN ('careers_page','greenhouse','lever','ashby')",
            name=op.f("ck_job_postings_source"),
        ),
        sa.CheckConstraint(
            "(source = 'careers_page' AND board_token IS NULL)"
            " OR (source <> 'careers_page' AND board_token IS NOT NULL)",
            name=op.f("ck_job_postings_board_token"),
        ),
        sa.CheckConstraint("status IN ('live','closed')", name=op.f("ck_job_postings_status")),
        sa.CheckConstraint(
            "(status = 'closed' AND closed_at IS NOT NULL)"
            " OR (status = 'live' AND closed_at IS NULL)",
            name=op.f("ck_job_postings_closed_at"),
        ),
    )
    op.create_index("ix_job_postings_lead_id", "job_postings", ["lead_id"])
    op.create_index("ix_job_postings_signal_id", "job_postings", ["signal_id"])
    with op.batch_alter_table("crawl_runs") as batch:
        for name in COUNTS:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

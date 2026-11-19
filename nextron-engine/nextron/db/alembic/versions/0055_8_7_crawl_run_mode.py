"""Story 8.7: re-check Leads on a schedule with immutable, superseding Evidence (FR-11, AD-7,
AD-10), in the ``research`` service.

Rebuilds ``crawl_runs`` (``batch_alter_table(recreate="always")`` from its exact table at 0054,
0020's columns and 0035's nine nullable counts, given as ``copy_from``, so nothing depends on
reflection) with the new column ``mode`` (16) last: why the run was made, one of
``nextron.domain.crawl.CrawlMode`` (``initial``, ``recheck_full`` or ``recheck_jobs``), NOT NULL
with the server default ``initial``, so every run stored before it reads ``initial``, and checked
by ``ck_crawl_runs_mode``. Both 0020 checks, the foreign key to ``leads`` and
``ix_crawl_runs_lead_id`` are kept. ``env.py`` runs with ``foreign_keys`` off, so the rebuild under
the foreign keys of ``crawled_pages``, ``ats_links`` and ``extraction_runs`` to ``crawl_runs`` is
safe.

Revision ID: 0055_8_7_crawl_run_mode
Revises: 0054_2_16_signal_inbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_8_7_crawl_run_mode"
down_revision: str | None = "0054_2_16_signal_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTS = (  # 0035_2_11_job_postings
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


def _crawl_runs_0054() -> sa.Table:
    """``crawl_runs`` exactly as ``0020_2_5_crawl`` created it and ``0035_2_11_job_postings``
    extended it (nothing after 0035 changes it)."""
    return sa.Table(
        "crawl_runs",
        sa.MetaData(),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("site_domain", sa.String(253), nullable=False),
        sa.Column("final_domain", sa.String(253), nullable=True),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("ended_at", sa.String(27), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), nullable=False),
        sa.Column("pages_skipped_robots", sa.Integer(), nullable=False),
        sa.Column("renders", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=True) for name in COUNTS),
        sa.PrimaryKeyConstraint("id", name="pk_crawl_runs"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_crawl_runs_lead_id_leads"),
        sa.CheckConstraint(
            "outcome IN ('ok','unreachable','robots_disallowed','lead_merged')",
            name="ck_crawl_runs_outcome",
        ),
        sa.CheckConstraint(
            "pages_fetched >= 0 AND pages_skipped_robots >= 0 AND renders >= 0 AND errors >= 0",
            name="ck_crawl_runs_counts",
        ),
        sa.Index("ix_crawl_runs_lead_id", "lead_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "crawl_runs", recreate="always", copy_from=_crawl_runs_0054()
    ) as batch:
        batch.add_column(
            sa.Column(
                "mode", sa.String(16), server_default=sa.text("'initial'"), nullable=False
            )
        )
        batch.create_check_constraint(
            op.f("ck_crawl_runs_mode"), "mode IN ('initial','recheck_full','recheck_jobs')"
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

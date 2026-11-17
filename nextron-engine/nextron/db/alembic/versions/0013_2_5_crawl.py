"""Story 2.5: the ``crawl_runs``, ``crawled_pages`` and ``ats_links`` tables (FR-10, AD-7, AD-17,
AD-18), owned by ``research``.

Creates ``crawl_runs``: a ULID ``id``, ``lead_id`` (``fk_crawl_runs_lead_id_leads``, indexed by
``ix_crawl_runs_lead_id``), ``site_domain``, a nullable ``final_domain``, ``started_at``,
``ended_at``, ``outcome`` (``ck_crawl_runs_outcome``: ``ok``, ``unreachable``,
``robots_disallowed`` or ``lead_merged``), a nullable ``error_code``, and the counts
``pages_fetched``, ``pages_skipped_robots``, ``renders`` and ``errors``
(``ck_crawl_runs_counts``: none negative).

Creates ``crawled_pages``: a ULID ``id``, ``lead_id`` (``fk_crawled_pages_lead_id_leads``),
``run_id`` (``fk_crawled_pages_run_id_crawl_runs``), ``url``, ``final_url``, ``kind``
(``ck_crawled_pages_kind``: the seven page kinds), ``fetched_at``, ``http_status``,
``content_hash``, a nullable ``text``, ``text_purge_after`` and ``changed_at``; unique on
``(lead_id, url)`` (``uq_crawled_pages_lead_id_url``).

Creates ``ats_links``: a ULID ``id``, ``lead_id`` (``fk_ats_links_lead_id_leads``), ``run_id``
(``fk_ats_links_run_id_crawl_runs``), ``page_id`` (``fk_ats_links_page_id_crawled_pages``),
``url``, ``host``, ``tag`` (``ck_ats_links_tag``: ``a``, ``iframe`` or ``script``),
``first_seen_at`` and ``last_seen_at``; unique on ``(lead_id, url)``
(``uq_ats_links_lead_id_url``).

Every column but the three nullable ones is NOT NULL, and no foreign key cascades.

Revision ID: 0013_2_5_crawl
Revises: 0012_2_8_ai_routes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_2_5_crawl"
down_revision: str | None = "0012_2_8_ai_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crawl_runs",
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
        sa.PrimaryKeyConstraint("id", name="pk_crawl_runs"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_crawl_runs_lead_id_leads"),
        sa.CheckConstraint(
            "outcome IN ('ok','unreachable','robots_disallowed','lead_merged')",
            name=op.f("ck_crawl_runs_outcome"),
        ),
        sa.CheckConstraint(
            "pages_fetched >= 0 AND pages_skipped_robots >= 0 AND renders >= 0 AND errors >= 0",
            name=op.f("ck_crawl_runs_counts"),
        ),
    )
    op.create_index("ix_crawl_runs_lead_id", "crawl_runs", ["lead_id"])
    op.create_table(
        "crawled_pages",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("run_id", sa.String(26), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("fetched_at", sa.String(27), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_purge_after", sa.String(27), nullable=False),
        sa.Column("changed_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crawled_pages"),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_crawled_pages_lead_id_leads"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_runs.id"], name="fk_crawled_pages_run_id_crawl_runs"
        ),
        sa.UniqueConstraint("lead_id", "url", name="uq_crawled_pages_lead_id_url"),
        sa.CheckConstraint(
            "kind IN ('home','team','about','careers','contact','services','news')",
            name=op.f("ck_crawled_pages_kind"),
        ),
    )
    op.create_table(
        "ats_links",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("lead_id", sa.String(26), nullable=False),
        sa.Column("run_id", sa.String(26), nullable=False),
        sa.Column("page_id", sa.String(26), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("tag", sa.String(8), nullable=False),
        sa.Column("first_seen_at", sa.String(27), nullable=False),
        sa.Column("last_seen_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ats_links"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_ats_links_lead_id_leads"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_runs.id"], name="fk_ats_links_run_id_crawl_runs"
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["crawled_pages.id"], name="fk_ats_links_page_id_crawled_pages"
        ),
        sa.UniqueConstraint("lead_id", "url", name="uq_ats_links_lead_id_url"),
        sa.CheckConstraint("tag IN ('a','iframe','script')", name=op.f("ck_ats_links_tag")),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

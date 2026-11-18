"""Story 2.13: finding a Contact's email address (FR-14, FR-51, FR-57, AD-7, AD-9, AD-10), all
owned by ``research``.

Creates ``email_domains``, what the engine learned about one registrable mail domain: a ULID
``id``; ``domain`` (unique: ``uq_email_domains_domain``); the address ``pattern`` (nullable, one
of ``nextron.domain.email_finding.Pattern``: ``ck_email_domains_pattern``), its
``pattern_source`` (nullable, ``published`` or ``hunter_domain_search``:
``ck_email_domains_pattern_source``) and ``pattern_learned_at`` (nullable), all three NULL or
all set (``ck_email_domains_pattern_learned``); ``pattern_evidence_ids`` (nullable, a JSON list
of the example Evidence ids), set exactly when the pattern was learned from published addresses
(``ck_email_domains_pattern_evidence``); ``domain_search_at`` (nullable: when Hunter's Domain
Search answered for the domain, at most once ever); ``created_at`` and ``updated_at``.

Adds to ``contacts`` the nullable ``email_search_tried`` (the paid finders tried, comma-joined
``hunter``/``prospeo`` in the order tried), ``email_search_outcome`` (one of
``nextron.domain.email_finding.EmailSearchOutcome``) and ``email_search_at``, with
``batch_alter_table`` and ``add_column`` only: SQLite adds them in place, without a rebuild, and
no CHECK constrains them (as Story 2.10's ``primary_set_by``).

Adds ``crawled_pages.mailto``, a nullable ``Text`` (a JSON list of the page's visible
``mailto:`` addresses, NULL when it has none), in place like ``tag_managers``.

Revision ID: 0030_2_13_email_domains
Revises: 0029_2_9_tag_managers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_2_13_email_domains"
down_revision: str | None = "0029_2_9_tag_managers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PATTERNS = (
    "'first.last','flast','first','firstlast','first_last','last.first','f.last'"
)


def upgrade() -> None:
    op.create_table(
        "email_domains",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("pattern", sa.String(16), nullable=True),
        sa.Column("pattern_source", sa.String(32), nullable=True),
        sa.Column("pattern_evidence_ids", sa.Text(), nullable=True),
        sa.Column("pattern_learned_at", sa.String(27), nullable=True),
        sa.Column("domain_search_at", sa.String(27), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_email_domains"),
        sa.UniqueConstraint("domain", name="uq_email_domains_domain"),
        sa.CheckConstraint(
            f"pattern IS NULL OR pattern IN ({PATTERNS})", name=op.f("ck_email_domains_pattern")
        ),
        sa.CheckConstraint(
            "pattern_source IS NULL OR pattern_source IN ('published','hunter_domain_search')",
            name=op.f("ck_email_domains_pattern_source"),
        ),
        sa.CheckConstraint(
            "(pattern IS NULL) = (pattern_source IS NULL) "
            "AND (pattern IS NULL) = (pattern_learned_at IS NULL)",
            name=op.f("ck_email_domains_pattern_learned"),
        ),
        sa.CheckConstraint(
            "(COALESCE(pattern_source, '') = 'published') = (pattern_evidence_ids IS NOT NULL)",
            name=op.f("ck_email_domains_pattern_evidence"),
        ),
    )
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("email_search_tried", sa.String(32), nullable=True))
        batch.add_column(sa.Column("email_search_outcome", sa.String(16), nullable=True))
        batch.add_column(sa.Column("email_search_at", sa.String(27), nullable=True))
    op.add_column("crawled_pages", sa.Column("mailto", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

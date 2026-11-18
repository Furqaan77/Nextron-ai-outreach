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

Drops and creates again ``trg_contacts_tombstone`` (0032, Story 3.13), the same except that the
three ``email_search_*`` columns join the columns an erased Contact keeps fixed (0032 asks each
later migration that adds a ``contacts`` column to do so): nothing writes a search to a
tombstone.

Revision ID: 0040_2_13_email_domains
Revises: 0039_6_4_inbox_warmup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_2_13_email_domains"
down_revision: str | None = "0039_6_4_inbox_warmup"
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
)

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
    # 0032's trigger, with the three new columns fixed on an erased Contact
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

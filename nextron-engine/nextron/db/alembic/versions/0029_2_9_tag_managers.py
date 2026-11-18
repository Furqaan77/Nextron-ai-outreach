"""Story 2.9: the tag managers each crawled page loads (FR-10, AD-7, AD-18), owned by
``research``, so the "contact form only" Absence is never claimed for a page whose chat widget a
tag manager may inject.

Adds ``crawled_pages.tag_managers``, a nullable ``Text`` (a canonical JSON array of
``nextron.domain.widgets.TAG_MANAGERS`` keys: ``[]`` when none was found), in place:
``ALTER TABLE ... ADD COLUMN``, no rebuild, so every existing page keeps its row and gets NULL,
meaning it was never checked.

Revision ID: 0029_2_9_tag_managers
Revises: 0028_2_10_primary_contact
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_2_9_tag_managers"
down_revision: str | None = "0028_2_10_primary_contact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crawled_pages", sa.Column("tag_managers", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

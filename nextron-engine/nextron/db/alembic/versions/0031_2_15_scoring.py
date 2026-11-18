"""Story 2.15: the score group of ``leads`` (FR-17, FR-62, AD-3, AD-7), owned by ``scoring``.

Adds to ``leads`` the nullable ``fit_score``, ``signal_score`` and ``lead_score`` (Integers),
``tier`` (1 character: ``A``, ``B`` or ``C``), ``tier_computed_at`` (UTC text, 27 characters),
``score_segment_version`` (the Segment version the score used), ``score_factors`` (the canonical
JSON of ``nextron.domain.scoring.factors_text``), ``disqualified_rule`` (a disqualifier key, at
most 31 characters) and ``disqualified_evidence`` (a JSON array of Evidence ids). Each is NULL
until ``score_lead`` first runs for the Lead. They are added with ``batch_alter_table`` and
``add_column`` only: SQLite adds them in place, without a rebuild, and no CHECK constrains them.

Revision ID: 0031_2_15_scoring
Revises: 0030_2_11_job_postings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_2_15_scoring"
down_revision: str | None = "0030_2_11_job_postings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("fit_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("signal_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("lead_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tier", sa.String(1), nullable=True))
        batch.add_column(sa.Column("tier_computed_at", sa.String(27), nullable=True))
        batch.add_column(sa.Column("score_segment_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("score_factors", sa.Text(), nullable=True))
        batch.add_column(sa.Column("disqualified_rule", sa.String(31), nullable=True))
        batch.add_column(sa.Column("disqualified_evidence", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

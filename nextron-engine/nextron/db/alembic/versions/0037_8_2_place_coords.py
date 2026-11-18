"""Story 8.2: a Google Places location on a Lead, kept at most 30 days (FR-5, FR-49, AD-17), in
the ``leads`` identity group owned by ``discovery``.

Adds to ``leads`` three nullable columns, all firm data (PII class ``none``):

- ``place_lat`` and ``place_lng`` (floats): the place's latitude and longitude;
- ``place_coords_at`` (27 characters, UTC text): when they were stored.

The Maps terms let coordinates be kept for at most 30 days: the daily ``purge_place_coords`` job
nulls all three after that and keeps ``place_id``. All are added with ``batch_alter_table`` and
``add_column`` only: SQLite adds them in place, without a rebuild, and no CHECK constrains them
(the code keeps them consistent).

Revision ID: 0037_8_2_place_coords
Revises: 0036_2_12_legal_form
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_8_2_place_coords"
down_revision: str | None = "0036_2_12_legal_form"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("place_lat", sa.Float(), nullable=True))
        batch.add_column(sa.Column("place_lng", sa.Float(), nullable=True))
        batch.add_column(sa.Column("place_coords_at", sa.String(27), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

"""Story 11.8: the Campaign and Variant triggers for dashboard editing (FR-3, AD-4, AD-21), on
tables owned by ``setup``. Triggers only: no column changes.

``trg_variants_at_most_two`` (migration 0021) capped every Variant row of a Campaign at 2. It is
dropped and created again as an **active-only** insert cap: an ``INSERT`` of an ``active``
Variant aborts when its Campaign already has 2 ``active`` ones. A retired (``inactive``) Variant
stays as history and never counts.

Three new fail-closed backstops:

- ``trg_variants_retired_final`` aborts an ``UPDATE`` that moves an ``inactive`` Variant to any
  other status: retiring a Variant is final.
- ``trg_variants_enrolled_frozen`` aborts an ``UPDATE`` that changes the ``name`` or the
  ``step_angles`` of a Variant any Enrollment names (any status), so its results stay
  comparable. Its status may still change (it may be retired).
- ``trg_campaigns_pair_frozen`` aborts an ``UPDATE`` that changes the ``segment_id`` or the
  ``market_id`` of a Campaign any Enrollment names (any status).

Revision ID: 0057_11_8_campaign_editing
Revises: 0056_11_7_sequence_editing
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_11_8_campaign_editing"
down_revision: str | None = "0056_11_7_sequence_editing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOO_MANY_VARIANTS = "a Campaign can have at most 2 active Variants"
RETIRED_FINAL = "a retired Variant never becomes active again"
ENROLLED_FROZEN = "the name and notes of a Variant with Enrollments never change"
PAIR_FROZEN = "the Segment and Market of a Campaign with Enrollments never change"


def upgrade() -> None:
    op.execute("DROP TRIGGER trg_variants_at_most_two")
    op.execute(
        "CREATE TRIGGER trg_variants_at_most_two BEFORE INSERT ON variants "
        "WHEN NEW.status = 'active' AND (SELECT COUNT(*) FROM variants "
        "WHERE campaign_id = NEW.campaign_id AND status = 'active') >= 2 "
        f"BEGIN SELECT RAISE(ABORT, '{TOO_MANY_VARIANTS}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_variants_retired_final BEFORE UPDATE ON variants "
        "WHEN OLD.status = 'inactive' AND NEW.status IS NOT 'inactive' "
        f"BEGIN SELECT RAISE(ABORT, '{RETIRED_FINAL}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_variants_enrolled_frozen BEFORE UPDATE ON variants "
        "WHEN (NEW.name IS NOT OLD.name OR NEW.step_angles IS NOT OLD.step_angles) "
        "AND EXISTS (SELECT 1 FROM enrollments WHERE variant_id = OLD.id) "
        f"BEGIN SELECT RAISE(ABORT, '{ENROLLED_FROZEN}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_campaigns_pair_frozen BEFORE UPDATE ON campaigns "
        "WHEN (NEW.segment_id IS NOT OLD.segment_id OR NEW.market_id IS NOT OLD.market_id) "
        "AND EXISTS (SELECT 1 FROM enrollments WHERE campaign_id = OLD.id) "
        f"BEGIN SELECT RAISE(ABORT, '{PAIR_FROZEN}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

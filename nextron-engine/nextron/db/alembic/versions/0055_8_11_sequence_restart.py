"""Story 8.11: restart a completed Sequence only on a new Event (FR-63, AD-3, AD-4), owned by
``sequencing``.

Adds the nullable ``restart_event_id`` to ``enrollments`` in place, with SQLite's ``ALTER TABLE
... ADD COLUMN`` (as ``0009_1_17_offpc`` does), so there is no table rebuild: every row, column,
check, index and trigger of ``enrollments`` is kept, and so are the foreign keys of ``drafts``
that name it. Existing rows get NULL. The column carries a named foreign key
(``fk_enrollments_restart_event_id_signals``, to ``signals.id``, no cascade): the new Event a
``restart`` Enrollment was restarted on, and ``ck_enrollments_restart_event``: it is set exactly
when ``origin`` is ``restart`` (no stored Enrollment has origin ``restart`` yet: nothing could
create one, so every existing row passes).

``trg_enrollments_bound`` is dropped and created again with ``restart_event_id`` joining 0038's
bound columns, so it never changes once inserted. ``trg_enrollments_erased_insert`` is left as
0038 made it.

Merge touchpoint: main's ``0056_11_7_sequence_editing`` adds ``enrollments.sequence_version_id``
(also ``ALTER TABLE``) and recreates ``trg_enrollments_bound``. When this migration is re-chained
after it, the trigger recreated here must also list ``sequence_version_id``.

Revision ID: 0055_8_11_sequence_restart
Revises: 0054_2_16_signal_inbox
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055_8_11_sequence_restart"
down_revision: str | None = "0054_2_16_signal_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOUND = (
    "id",
    "contact_id",
    "campaign_id",
    "variant_id",
    "sending_inbox_id",
    "sequence_id",
    "sequence_version",
    "offer_id",
    "origin",
    "created_at",
    "restart_event_id",  # Story 8.11
)
BOUND_MESSAGE = "the bound columns of an Enrollment never change"  # 0038's message
RESTART_EVENT_CHECK = "(origin = 'restart') = (restart_event_id IS NOT NULL)"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE enrollments ADD COLUMN restart_event_id VARCHAR(26)\n"
        "CONSTRAINT fk_enrollments_restart_event_id_signals REFERENCES signals (id)\n"
        f"CONSTRAINT ck_enrollments_restart_event CHECK ({RESTART_EVENT_CHECK})"
    )
    op.execute("DROP TRIGGER trg_enrollments_bound")
    changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in BOUND)
    op.execute(
        f"CREATE TRIGGER trg_enrollments_bound BEFORE UPDATE ON enrollments WHEN {changed} "
        f"BEGIN SELECT RAISE(ABORT, '{BOUND_MESSAGE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

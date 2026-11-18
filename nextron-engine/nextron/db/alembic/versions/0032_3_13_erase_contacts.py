"""Story 3.13: erase a Contact on request (FR-52, FR-50, AD-4, AD-16, AD-17).

Creates ``data_subject_requests`` (owner ``compliance``): a ULID ``id``; ``contact_id``
(``fk_data_subject_requests_contact_id_contacts``, no cascade, indexed by
``ix_data_subject_requests_contact_id``); ``kind`` (``ck_data_subject_requests_kind``: ``erase``
or ``export``); ``status`` (``ck_data_subject_requests_status``: ``open`` or ``completed``);
``request_ref`` (``ck_data_subject_requests_request_ref``: 1 to 200 characters); ``opened_at``;
the nullable ``completed_at``, set exactly on a ``completed`` request
(``ck_data_subject_requests_completed``); and the nullable ``receipt_id`` (a ULID; no foreign key
until Story 3.14 creates the receipts). Every other column is NOT NULL. Three triggers, after
``suppression_entries`` (migration 0022), keep it the proof of erasure:

- ``trg_data_subject_requests_no_delete`` aborts any ``DELETE``;
- ``trg_data_subject_requests_insert_open`` aborts an ``INSERT`` that isn't ``open``, or whose
  ``id`` already exists (so ``INSERT OR REPLACE`` can't replace a request);
- ``trg_data_subject_requests_immutable`` aborts an ``UPDATE`` that changes ``id``,
  ``contact_id``, ``kind``, ``request_ref`` or ``opened_at``; changes ``status`` other than from
  ``open`` to ``completed``; changes ``completed_at`` without that status change; or changes a
  ``receipt_id`` that is set (it is set at most once).

Adds ``contacts.erased_at``, a nullable UTC time (the Contact is a tombstone once it is set), in
place: ``ALTER TABLE ... ADD COLUMN``, no rebuild, so every Contact keeps its row and gets NULL.
``trg_contacts_tombstone`` aborts an ``UPDATE`` that sets ``erased_at`` unless the new row is a
tombstone (``name`` and ``role`` NULL or ``[erased]``; ``linkedin_url``, ``office_country`` and
``office_region`` NULL; ``email`` NULL or 64 lowercase hex characters), and any ``UPDATE`` of an
erased Contact that changes a column other than ``lead_id``, ``is_primary`` and ``updated_at``
(a merge moves it; nothing refills it). ``primary_set_by`` (migration 0028, Story 2.10) is fixed
too: the erasure clears it with ``is_primary``, and a merge or a cleared Primary writes NULL
again, so only making the tombstone Primary (``primary_set_by`` ``system`` or ``operator``)
would change it, and that is refused. A later migration that adds a ``contacts`` column must
create this trigger again with that column in it.

``trg_provenance_records_erased_update`` and ``trg_provenance_records_erased_insert`` abort an
``UPDATE`` or ``INSERT`` of a Provenance Record whose Contact (old or new) is erased. They are
created on the table that 0031 (Story 3.7) rebuilt, so no later rebuild has dropped them.

``trg_evidence_immutable`` (migration 0025) is dropped and created again, the same except that
``quote`` and ``value`` may each change, from non-NULL, only to ``[erased]``, on a row whose
``person_contact_id`` is set (the erasure tombstone, AD-7). ``person_contact_id`` stays fixed,
so every other change is still refused.

Revision ID: 0032_3_13_erase_contacts
Revises: 0031_3_7_basis_rule_width
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_3_13_erase_contacts"
down_revision: str | None = "0031_3_7_basis_rule_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REDACTED = "[erased]"  # nextron.domain.erasure.REDACTED
REQUESTS_PERMANENT = "data subject requests are permanent"
TOMBSTONE = "an erased Contact is a tombstone"
ERASED_PROVENANCE = "the Provenance of an erased Contact is a tombstone"
EVIDENCE_IMMUTABLE = "Evidence is immutable"  # 0025's message
REQUEST_FIXED = ("id", "contact_id", "kind", "request_ref", "opened_at")
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
    "primary_set_by",  # 0028 (Story 2.10): NULL on a tombstone, and never set again
    "erased_at",
)
# 0025's FIXED_COLUMNS less quote and value, which an erasure may tombstone
EVIDENCE_FIXED = (
    "id",
    "type",
    "sought",
    "number",
    "fact",
    "pages_checked",
    "source",
    "source_url",
    "retrieved_at",
    "extracted_by",
    "person_contact_id",
    "created_at",
)


def _changed(columns: Sequence[str]) -> str:
    return " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)


def _redaction_only(column: str) -> str:
    """The SQL of a change to ``column`` other than the erasure tombstone."""
    return (
        f"(NEW.{column} IS NOT OLD.{column} AND NOT (OLD.{column} IS NOT NULL "
        f"AND NEW.{column} IS '{REDACTED}' AND OLD.person_contact_id IS NOT NULL))"
    )


def upgrade() -> None:
    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("contact_id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_ref", sa.String(200), nullable=False),
        sa.Column("opened_at", sa.String(27), nullable=False),
        sa.Column("completed_at", sa.String(27), nullable=True),
        sa.Column("receipt_id", sa.String(26), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_data_subject_requests"),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name="fk_data_subject_requests_contact_id_contacts",
        ),
        sa.CheckConstraint(
            "kind IN ('erase','export')", name=op.f("ck_data_subject_requests_kind")
        ),
        sa.CheckConstraint(
            "status IN ('open','completed')", name=op.f("ck_data_subject_requests_status")
        ),
        sa.CheckConstraint(
            "length(request_ref) BETWEEN 1 AND 200",
            name=op.f("ck_data_subject_requests_request_ref"),
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_data_subject_requests_completed"),
        ),
    )
    op.create_index(
        "ix_data_subject_requests_contact_id", "data_subject_requests", ["contact_id"]
    )
    op.execute(
        "CREATE TRIGGER trg_data_subject_requests_no_delete BEFORE DELETE ON "
        f"data_subject_requests BEGIN SELECT RAISE(ABORT, '{REQUESTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_data_subject_requests_insert_open BEFORE INSERT ON "
        "data_subject_requests WHEN NEW.status IS NOT 'open' "
        "OR EXISTS (SELECT 1 FROM data_subject_requests WHERE id = NEW.id) "
        f"BEGIN SELECT RAISE(ABORT, '{REQUESTS_PERMANENT}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_data_subject_requests_immutable BEFORE UPDATE ON "
        f"data_subject_requests WHEN {_changed(REQUEST_FIXED)} "
        "OR (NEW.status IS NOT OLD.status "
        "AND NOT (OLD.status IS 'open' AND NEW.status IS 'completed')) "
        "OR (NEW.completed_at IS NOT OLD.completed_at AND NEW.status IS OLD.status) "
        "OR (OLD.receipt_id IS NOT NULL AND NEW.receipt_id IS NOT OLD.receipt_id) "
        f"BEGIN SELECT RAISE(ABORT, '{REQUESTS_PERMANENT}'); END"
    )
    op.add_column("contacts", sa.Column("erased_at", sa.String(27), nullable=True))
    tombstone = (
        f"(NEW.name IS NULL OR NEW.name IS '{REDACTED}') "
        f"AND (NEW.role IS NULL OR NEW.role IS '{REDACTED}') "
        "AND NEW.linkedin_url IS NULL AND NEW.office_country IS NULL "
        "AND NEW.office_region IS NULL "
        "AND (NEW.email IS NULL "
        "OR (length(NEW.email) = 64 AND NEW.email NOT GLOB '*[^0-9a-f]*'))"
    )
    op.execute(
        "CREATE TRIGGER trg_contacts_tombstone BEFORE UPDATE ON contacts "
        f"WHEN (OLD.erased_at IS NULL AND NEW.erased_at IS NOT NULL AND NOT ({tombstone})) "
        f"OR (OLD.erased_at IS NOT NULL AND ({_changed(CONTACT_FIXED)})) "
        f"BEGIN SELECT RAISE(ABORT, '{TOMBSTONE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_provenance_records_erased_update BEFORE UPDATE ON "
        "provenance_records WHEN EXISTS (SELECT 1 FROM contacts "
        "WHERE id IN (OLD.contact_id, NEW.contact_id) AND erased_at IS NOT NULL) "
        f"BEGIN SELECT RAISE(ABORT, '{ERASED_PROVENANCE}'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_provenance_records_erased_insert BEFORE INSERT ON "
        "provenance_records WHEN EXISTS (SELECT 1 FROM contacts "
        "WHERE id = NEW.contact_id AND erased_at IS NOT NULL) "
        f"BEGIN SELECT RAISE(ABORT, '{ERASED_PROVENANCE}'); END"
    )
    op.execute("DROP TRIGGER trg_evidence_immutable")
    op.execute(
        "CREATE TRIGGER trg_evidence_immutable BEFORE UPDATE ON evidence "
        f"WHEN {_changed(EVIDENCE_FIXED)} "
        f"OR {_redaction_only('quote')} "
        f"OR {_redaction_only('value')} "
        "OR (OLD.superseded_by IS NOT NULL AND NEW.superseded_by IS NOT OLD.superseded_by) "
        "OR (OLD.retracted_at IS NOT NULL AND NEW.retracted_at IS NOT OLD.retracted_at) "
        f"BEGIN SELECT RAISE(ABORT, '{EVIDENCE_IMMUTABLE}'); END"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

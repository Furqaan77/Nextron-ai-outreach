"""Story 2.12: a UK Lead's legal form, confirmed through Companies House (FR-12, FR-9, NFR-3,
AD-7), in the ``leads`` profile group owned by ``research``.

Adds to ``leads`` five nullable columns, all firm data (PII class ``none``):

- ``legal_form`` (64 characters): the linked record's Companies House company type, such as
  ``ltd``, or NULL;
- ``company_status`` (32 characters): its company status, such as ``active``, or NULL;
- ``legal_form_status`` (16 characters): ``confirmed``, ``not_corporate`` or ``unconfirmed``
  (``nextron.domain.legal_form.LegalFormStatus``); NULL until the Lead's legal form is first
  checked, which Eligibility reads as unconfirmed;
- ``legal_form_site_evidence_id`` and ``legal_form_registry_evidence_id`` (26 characters): the
  ``evidence`` rows the finding rests on (the website quote and the registry record), with no
  foreign key, like ``hq_evidence_id`` (one would make a ``leads``/``evidence`` cycle).

All are added with ``batch_alter_table`` and ``add_column`` only: SQLite adds them in place,
without a rebuild, and no CHECK constrains them (the code keeps them consistent).

Revision ID: 0036_2_12_legal_form
Revises: 0035_2_11_job_postings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_2_12_legal_form"
down_revision: str | None = "0035_2_11_job_postings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("legal_form", sa.String(64), nullable=True))
        batch.add_column(sa.Column("company_status", sa.String(32), nullable=True))
        batch.add_column(sa.Column("legal_form_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("legal_form_site_evidence_id", sa.String(26), nullable=True))
        batch.add_column(
            sa.Column("legal_form_registry_evidence_id", sa.String(26), nullable=True)
        )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

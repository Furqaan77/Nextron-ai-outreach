"""Story 6.6: each Sending Domain check's DNSBL and Postmaster spam-rate results (FR-38, FR-58,
AD-9), in ``domain_checks``, owned by ``sending``.

Rebuilds ``domain_checks`` with three new nullable columns, all PII class ``none``:

- ``dnsbl_result`` (text): ``nextron.domain.reputation.encode_dnsbl`` of ``{zone: outcome}``;
- ``spam_rate`` (integer): the day's user-reported spam rate in parts per million, NULL when
  Postmaster had no data (never 0 for no data);
- ``spam_rate_status`` (16 characters): ``ok``, ``alert``, ``pause`` or ``no_data``
  (``ck_domain_checks_spam_rate_status``, a table check like ``ck_domain_checks_passed``).

Rows recorded before this migration keep NULL in all three; every new row sets them. The
rebuild is hand-written SQL rather than ``batch_alter_table``, so the copy carries each row's
``rowid`` explicitly: ``repos.sending.checks`` reads a domain's newest check by insertion
order (the highest ``rowid``), and a rebuild that renumbered the rows could reorder them. The
table, its checks, its foreign key to ``sending_domains`` and
``ix_domain_checks_sending_domain_id_checked_at`` are recreated exactly as 0033 made them, plus
the new columns and check. No table references ``domain_checks``, and ``env.py`` runs with
``foreign_keys`` off, so the rebuild is safe.

Revision ID: 0047_6_6_reputation
Revises: 0046_6_11_inbox_pacing
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_6_6_reputation"
down_revision: str | None = "0046_6_11_inbox_pacing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS_0033 = (
    "id, sending_domain_id, checked_at, spf_ok, dkim_ok, dmarc_ok, aligned_ok, passed, details"
)
CREATE = """CREATE TABLE _new_domain_checks (
\tid VARCHAR(26) NOT NULL,
\tsending_domain_id VARCHAR(26) NOT NULL,
\tchecked_at VARCHAR(27) NOT NULL,
\tspf_ok BOOLEAN NOT NULL,
\tdkim_ok BOOLEAN NOT NULL,
\tdmarc_ok BOOLEAN NOT NULL,
\taligned_ok BOOLEAN NOT NULL,
\tpassed BOOLEAN NOT NULL,
\tdetails VARCHAR(256) NOT NULL,
\tdnsbl_result TEXT,
\tspam_rate INTEGER,
\tspam_rate_status VARCHAR(16),
\tCONSTRAINT pk_domain_checks PRIMARY KEY (id),
\tCONSTRAINT ck_domain_checks_passed CHECK (passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)),
\tCONSTRAINT ck_domain_checks_spam_rate_status CHECK (spam_rate_status IN ('ok','alert','pause','no_data')),
\tCONSTRAINT fk_domain_checks_sending_domain_id_sending_domains FOREIGN KEY(sending_domain_id) REFERENCES sending_domains (id)
)"""  # noqa: E501 - one constraint per line, as the schema contracts read them


def upgrade() -> None:
    op.execute(CREATE)
    op.execute(
        f"INSERT INTO _new_domain_checks (rowid, {COLUMNS_0033}) "
        f"SELECT rowid, {COLUMNS_0033} FROM domain_checks"
    )
    op.execute("DROP TABLE domain_checks")
    op.execute("ALTER TABLE _new_domain_checks RENAME TO domain_checks")
    op.execute(
        "CREATE INDEX ix_domain_checks_sending_domain_id_checked_at "
        "ON domain_checks (sending_domain_id, checked_at)"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "migrations are forward-only; recover from a bad upgrade with the pre_migrate backup"
    )

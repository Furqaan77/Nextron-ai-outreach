"""The ``email_domains`` table's repository (Story 2.13, FR-14, AD-3): the only code that writes
it. It checks nothing; ``nextron.app.research.finding`` does.

- ``StoredEmailDomain``: one row (``pattern_evidence_ids`` decoded to a tuple, ``()`` for NULL);
- ``get(domain)``: the row for the registrable mail domain ``domain``, or ``None``;
- ``ensure(domain, at) -> StoredEmailDomain``: the row, inserted (created and updated at ``at``,
  nothing learned) when there is none;
- ``set_pattern(domain_id, *, pattern, source, evidence_ids, at)``: stores the pattern, its
  source, its example Evidence ids (a JSON list; ``None`` for a pattern not learned from
  published addresses) and ``pattern_learned_at`` = ``updated_at`` = ``at``, only on a row that
  has no pattern yet (a stored pattern is never overwritten); whether it wrote;
- ``set_domain_search_at(domain_id, at)``: records that Hunter's Domain Search answered.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.research import EmailDomainRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredEmailDomain:
    """One ``email_domains`` row."""

    id: str
    domain: str
    pattern: str | None
    pattern_source: str | None
    pattern_evidence_ids: tuple[str, ...]
    pattern_learned_at: datetime | None
    domain_search_at: datetime | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    EmailDomainRecord.id,
    EmailDomainRecord.domain,
    EmailDomainRecord.pattern,
    EmailDomainRecord.pattern_source,
    EmailDomainRecord.pattern_evidence_ids,
    EmailDomainRecord.pattern_learned_at,
    EmailDomainRecord.domain_search_at,
    EmailDomainRecord.created_at,
    EmailDomainRecord.updated_at,
)


def _ids(text: str | None) -> tuple[str, ...]:
    if text is None:
        return ()
    decoded = json.loads(text)
    return tuple(str(value) for value in decoded) if isinstance(decoded, list) else ()


def get(domain: str) -> StoredEmailDomain | None:
    """The row of the mail domain ``domain``, or ``None``."""
    statement = select(*_COLUMNS).where(EmailDomainRecord.domain == domain)
    row = current().session.execute(statement).one_or_none()
    if row is None:
        return None
    values = list(row)
    values[4] = _ids(values[4])
    return StoredEmailDomain(*values)


def ensure(domain: str, at: datetime) -> StoredEmailDomain:
    """The row of the mail domain ``domain``, inserted when there is none."""
    stored = get(domain)
    if stored is not None:
        return stored
    current().session.execute(
        insert_rows(EmailDomainRecord).values(
            id=new_id(), domain=domain, created_at=at, updated_at=at
        )
    )
    inserted = get(domain)
    if inserted is None:  # the insert just ran in this transaction
        raise RuntimeError("the email domain row was not stored")
    return inserted


def set_pattern(
    domain_id: str,
    *,
    pattern: str,
    source: str,
    evidence_ids: Sequence[str] | None,
    at: datetime,
) -> bool:
    """Store the domain's pattern when it has none; whether it wrote."""
    result = current().session.execute(
        update_rows(EmailDomainRecord)
        .where(EmailDomainRecord.id == domain_id, EmailDomainRecord.pattern.is_(None))
        .values(
            pattern=pattern,
            pattern_source=source,
            pattern_evidence_ids=None if evidence_ids is None else json.dumps(list(evidence_ids)),
            pattern_learned_at=at,
            updated_at=at,
        )
    )
    return bool(result.rowcount)


def set_domain_search_at(domain_id: str, at: datetime) -> None:
    """Record that Hunter's Domain Search answered for the domain at ``at``."""
    current().session.execute(
        update_rows(EmailDomainRecord)
        .where(EmailDomainRecord.id == domain_id)
        .values(domain_search_at=at, updated_at=at)
    )

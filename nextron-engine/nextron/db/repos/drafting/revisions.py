"""The ``draft_revisions`` repository (FR-20, AD-3, AD-6, AD-17; Story 4.12): the only code that
writes a Draft's text.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.drafting`` does. A
revision is immutable: there is no update or delete, and the table's triggers refuse one. The
one exception is ``tombstone``, the erasure (AD-17), which the triggers allow.

- ``StoredRevision``: one row, ``evidence_ids`` and ``fact_versions`` decoded from JSON (a
  tuple of ids; a tuple of ``(fact_id, version)`` pairs);
- ``NewRevision(draft_id, rev, subject, body, footer_snapshot_id, evidence_ids, fact_versions,
  cited_signal_id, origin, route_tuple_id, playbook_sha256, playbook_version, prompt_version,
  instruction, text_hash)`` and ``insert(revision, at) -> id``: one new row created at ``at``;
  its new ULID;
- ``get(draft_id, rev)``: the row, or ``None``;
- ``of_draft(draft_id)``: the Draft's revisions, by ``rev``;
- ``of_drafts(draft_ids) -> dict[draft_id, tuple[StoredRevision, ...]]``: the same for several
  Drafts (a Draft without a revision is absent);
- ``tombstone(draft_ids) -> int``: every revision of those Drafts gets ``subject`` and ``body``
  ``[erased]`` and a set ``instruction`` ``[erased]`` (rows, hashes and traceability kept); the
  number of rows matched. Running it again changes nothing.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Row, case, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.drafting import DraftRevisionRecord
from nextron.db.uow import current
from nextron.domain.erasure import REDACTED

type FactVersion = tuple[str, int]


@dataclass(frozen=True, slots=True)
class NewRevision:
    """One revision ``insert`` stores."""

    draft_id: str
    rev: int
    subject: str
    body: str
    footer_snapshot_id: str
    evidence_ids: tuple[str, ...]
    fact_versions: tuple[FactVersion, ...]
    cited_signal_id: str | None
    origin: str
    route_tuple_id: str | None
    playbook_sha256: str | None
    playbook_version: str | None
    prompt_version: str | None
    instruction: str | None
    text_hash: str


@dataclass(frozen=True, slots=True)
class StoredRevision:
    """One ``draft_revisions`` row."""

    id: str
    draft_id: str
    rev: int
    subject: str
    body: str
    footer_snapshot_id: str
    evidence_ids: tuple[str, ...]
    fact_versions: tuple[FactVersion, ...]
    cited_signal_id: str | None
    origin: str
    route_tuple_id: str | None
    playbook_sha256: str | None
    playbook_version: str | None
    prompt_version: str | None
    instruction: str | None
    text_hash: str
    created_at: datetime


_COLUMNS: Final = (
    DraftRevisionRecord.id,
    DraftRevisionRecord.draft_id,
    DraftRevisionRecord.rev,
    DraftRevisionRecord.subject,
    DraftRevisionRecord.body,
    DraftRevisionRecord.footer_snapshot_id,
    DraftRevisionRecord.evidence_ids,
    DraftRevisionRecord.fact_versions,
    DraftRevisionRecord.cited_signal_id,
    DraftRevisionRecord.origin,
    DraftRevisionRecord.route_tuple_id,
    DraftRevisionRecord.playbook_sha256,
    DraftRevisionRecord.playbook_version,
    DraftRevisionRecord.prompt_version,
    DraftRevisionRecord.instruction,
    DraftRevisionRecord.text_hash,
    DraftRevisionRecord.created_at,
)


def _stored(row: Row[Any]) -> StoredRevision:
    evidence = json.loads(row.evidence_ids)
    facts = json.loads(row.fact_versions)
    return StoredRevision(
        id=row.id,
        draft_id=row.draft_id,
        rev=row.rev,
        subject=row.subject,
        body=row.body,
        footer_snapshot_id=row.footer_snapshot_id,
        evidence_ids=tuple(str(each) for each in evidence),
        fact_versions=tuple((str(fact_id), int(version)) for fact_id, version in facts),
        cited_signal_id=row.cited_signal_id,
        origin=row.origin,
        route_tuple_id=row.route_tuple_id,
        playbook_sha256=row.playbook_sha256,
        playbook_version=row.playbook_version,
        prompt_version=row.prompt_version,
        instruction=row.instruction,
        text_hash=row.text_hash,
        created_at=row.created_at,
    )


def insert(revision: NewRevision, at: datetime) -> str:
    """Store ``revision``, created at ``at``; its new ULID."""
    revision_id = new_id()
    current().session.execute(
        insert_rows(DraftRevisionRecord).values(
            id=revision_id,
            draft_id=revision.draft_id,
            rev=revision.rev,
            subject=revision.subject,
            body=revision.body,
            footer_snapshot_id=revision.footer_snapshot_id,
            evidence_ids=json.dumps(list(revision.evidence_ids)),
            fact_versions=json.dumps([list(pair) for pair in revision.fact_versions]),
            cited_signal_id=revision.cited_signal_id,
            origin=revision.origin,
            route_tuple_id=revision.route_tuple_id,
            playbook_sha256=revision.playbook_sha256,
            playbook_version=revision.playbook_version,
            prompt_version=revision.prompt_version,
            instruction=revision.instruction,
            text_hash=revision.text_hash,
            created_at=at,
        )
    )
    return revision_id


def get(draft_id: str, rev: int) -> StoredRevision | None:
    """The revision ``rev`` of the Draft ``draft_id``, or ``None``."""
    statement = select(*_COLUMNS).where(
        DraftRevisionRecord.draft_id == draft_id, DraftRevisionRecord.rev == rev
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def of_draft(draft_id: str) -> tuple[StoredRevision, ...]:
    """The Draft's revisions, by ``rev``."""
    statement = (
        select(*_COLUMNS)
        .where(DraftRevisionRecord.draft_id == draft_id)
        .order_by(DraftRevisionRecord.rev)
    )
    return tuple(_stored(row) for row in current().session.execute(statement))


def of_drafts(draft_ids: Iterable[str]) -> dict[str, tuple[StoredRevision, ...]]:
    """Each Draft's revisions, by ``rev``; a Draft without a revision is absent."""
    chosen = sorted(set(draft_ids))
    if not chosen:
        return {}
    statement = (
        select(*_COLUMNS)
        .where(DraftRevisionRecord.draft_id.in_(chosen))
        .order_by(DraftRevisionRecord.draft_id, DraftRevisionRecord.rev)
    )
    found: dict[str, list[StoredRevision]] = {}
    for row in current().session.execute(statement):
        found.setdefault(row.draft_id, []).append(_stored(row))
    return {draft_id: tuple(rows) for draft_id, rows in found.items()}


def tombstone(draft_ids: Iterable[str]) -> int:
    """Redact the text of every revision of ``draft_ids`` (the erasure); the rows matched."""
    chosen = sorted(set(draft_ids))
    if not chosen:
        return 0
    instruction = DraftRevisionRecord.instruction
    result = current().session.execute(
        update_rows(DraftRevisionRecord)
        .where(DraftRevisionRecord.draft_id.in_(chosen))
        .values(
            subject=REDACTED,
            body=REDACTED,
            instruction=case((instruction.is_(None), None), else_=REDACTED),
        )
    )
    return int(result.rowcount)  # type: ignore[attr-defined]

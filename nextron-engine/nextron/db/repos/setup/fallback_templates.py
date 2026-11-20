"""The ``fallback_templates`` table's repository (FR-22, AD-3, AD-12; Story 4.19): the only code
that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It checks nothing; ``nextron.app.setup`` does.

- ``all_templates()``: every row as a ``StoredFallbackTemplate``, by Segment id, Step and
  English variant;
- ``find(segment_id, step, english_variant)``: that row, or ``None``;
- ``insert(segment_id, step, english_variant, subject, body, at) -> id``: a new row created and
  updated at ``at``;
- ``update(template_id, subject, body, at)``: sets its text and ``updated_at``;
- ``delete(template_id)``: removes it (nothing references a template: a revision filled from one
  keeps its own text).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy import delete as delete_rows
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import FallbackTemplateRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredFallbackTemplate:
    """One ``fallback_templates`` row."""

    id: str
    segment_id: str
    step: int
    english_variant: str
    subject: str | None
    body: str
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    FallbackTemplateRecord.id,
    FallbackTemplateRecord.segment_id,
    FallbackTemplateRecord.step,
    FallbackTemplateRecord.english_variant,
    FallbackTemplateRecord.subject,
    FallbackTemplateRecord.body,
    FallbackTemplateRecord.created_at,
    FallbackTemplateRecord.updated_at,
)


def _stored(row: Row[Any]) -> StoredFallbackTemplate:
    return StoredFallbackTemplate(
        id=row.id,
        segment_id=row.segment_id,
        step=row.step,
        english_variant=row.english_variant,
        subject=row.subject,
        body=row.body,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def all_templates() -> tuple[StoredFallbackTemplate, ...]:
    """Every stored template, by Segment id, Step and English variant."""
    statement = select(*_COLUMNS).order_by(
        FallbackTemplateRecord.segment_id,
        FallbackTemplateRecord.step,
        FallbackTemplateRecord.english_variant,
    )
    return tuple(_stored(row) for row in current().session.execute(statement))


def find(segment_id: str, step: int, english_variant: str) -> StoredFallbackTemplate | None:
    """The template of that Segment, Step and English variant, or ``None``."""
    statement = select(*_COLUMNS).where(
        FallbackTemplateRecord.segment_id == segment_id,
        FallbackTemplateRecord.step == step,
        FallbackTemplateRecord.english_variant == english_variant,
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def insert(
    segment_id: str,
    step: int,
    english_variant: str,
    subject: str | None,
    body: str,
    at: datetime,
) -> str:
    """Store a new template, created at ``at``; its new ULID."""
    template_id = new_id()
    current().session.execute(
        insert_rows(FallbackTemplateRecord).values(
            id=template_id,
            segment_id=segment_id,
            step=step,
            english_variant=english_variant,
            subject=subject,
            body=body,
            created_at=at,
            updated_at=at,
        )
    )
    return template_id


def update(template_id: str, subject: str | None, body: str, at: datetime) -> None:
    """Set the subject and body of the template ``template_id``, changed at ``at``."""
    current().session.execute(
        update_rows(FallbackTemplateRecord)
        .where(FallbackTemplateRecord.id == template_id)
        .values(subject=subject, body=body, updated_at=at)
    )


def delete(template_id: str) -> None:
    """Remove the template ``template_id``."""
    current().session.execute(
        delete_rows(FallbackTemplateRecord).where(FallbackTemplateRecord.id == template_id)
    )

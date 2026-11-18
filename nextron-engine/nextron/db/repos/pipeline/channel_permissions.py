"""The ``channel_permissions`` repository (FR-24, FR-47, AD-3; Story 10.7): the only code that
writes the Channel Permissions a prospect gave on WhatsApp or Instagram.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` does, and the
table's CHECKs and partial unique index back it.

- ``StoredPermission``: one row;
- ``for_contact(contact_id)``: the Contact's permissions, newest first (by ``created_at``, then
  ``id``);
- ``get(permission_id)``: one permission, or ``None``;
- ``active_id(contact_id, channel)``: the id of the Contact's ``active`` permission on
  ``channel``, or ``None``;
- ``insert(...) -> id``: a new ``active`` row;
- ``set_inbound(permission_id, at, updated_at)``: a new ``last_inbound_at``;
- ``revoke(permission_id, at)``: ``revoked``, with ``revoked_at`` and ``updated_at`` ``at``;
- ``clear_identifiers(contact_id, at)``: NULLs the number and handle of each of the Contact's
  rows (erasure), with ``updated_at`` ``at``; how many rows it changed;
- ``export_rows(contact_id)``: every column of each of the Contact's rows, oldest first.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.pipeline import ChannelPermissionRecord
from nextron.db.uow import current
from nextron.domain.channel_permission import RECORDED_BY, PermissionStatus


@dataclass(frozen=True, slots=True)
class StoredPermission:
    """One ``channel_permissions`` row."""

    id: str
    contact_id: str
    channel: str
    prospect_action: str
    occurred_at: datetime
    evidence_id: str
    whatsapp_number: str | None
    instagram_handle: str | None
    last_inbound_at: datetime | None
    status: str
    revoked_at: datetime | None
    recorded_by: str
    created_at: datetime
    updated_at: datetime


_R = ChannelPermissionRecord
_COLUMNS = (
    _R.id,
    _R.contact_id,
    _R.channel,
    _R.prospect_action,
    _R.occurred_at,
    _R.evidence_id,
    _R.whatsapp_number,
    _R.instagram_handle,
    _R.last_inbound_at,
    _R.status,
    _R.revoked_at,
    _R.recorded_by,
    _R.created_at,
    _R.updated_at,
)


def for_contact(contact_id: str) -> list[StoredPermission]:
    """The Contact's permissions, newest first."""
    rows = current().session.execute(
        select(*_COLUMNS)
        .where(_R.contact_id == contact_id)
        .order_by(_R.created_at.desc(), _R.id.desc())
    )
    return [StoredPermission(*row) for row in rows]


def get(permission_id: str) -> StoredPermission | None:
    """The permission ``permission_id``, or ``None``."""
    row = current().session.execute(select(*_COLUMNS).where(_R.id == permission_id)).one_or_none()
    return None if row is None else StoredPermission(*row)


def active_id(contact_id: str, channel: str) -> str | None:
    """The id of the Contact's ``active`` permission on ``channel``, or ``None``."""
    found = current().session.execute(
        select(_R.id).where(
            _R.contact_id == contact_id,
            _R.channel == channel,
            _R.status == PermissionStatus.ACTIVE.value,
        )
    )
    return found.scalar_one_or_none()


def insert(
    *,
    contact_id: str,
    channel: str,
    prospect_action: str,
    occurred_at: datetime,
    evidence_id: str,
    whatsapp_number: str | None,
    instagram_handle: str | None,
    last_inbound_at: datetime | None,
    at: datetime,
) -> str:
    """Store a new ``active`` permission recorded ``at``; return its new ULID."""
    permission_id = new_id()
    current().session.execute(
        insert_rows(_R).values(
            id=permission_id,
            contact_id=contact_id,
            channel=channel,
            prospect_action=prospect_action,
            occurred_at=occurred_at,
            evidence_id=evidence_id,
            whatsapp_number=whatsapp_number,
            instagram_handle=instagram_handle,
            last_inbound_at=last_inbound_at,
            status=PermissionStatus.ACTIVE.value,
            revoked_at=None,
            recorded_by=RECORDED_BY,
            created_at=at,
            updated_at=at,
        )
    )
    return permission_id


def set_inbound(permission_id: str, at: datetime, updated_at: datetime) -> None:
    """Set the permission's ``last_inbound_at`` to ``at``."""
    current().session.execute(
        update_rows(_R)
        .where(_R.id == permission_id)
        .values(last_inbound_at=at, updated_at=updated_at)
    )


def revoke(permission_id: str, at: datetime) -> None:
    """Make the permission ``revoked`` at ``at``."""
    current().session.execute(
        update_rows(_R)
        .where(_R.id == permission_id)
        .values(status=PermissionStatus.REVOKED.value, revoked_at=at, updated_at=at)
    )


def clear_identifiers(contact_id: str, at: datetime) -> int:
    """NULL the number and handle of each of the Contact's rows; how many it changed."""
    session = current().session
    held = (_R.whatsapp_number.is_not(None)) | (_R.instagram_handle.is_not(None))
    ids = list(session.execute(select(_R.id).where(_R.contact_id == contact_id, held)).scalars())
    if ids:
        session.execute(
            update_rows(_R)
            .where(_R.id.in_(ids))
            .values(whatsapp_number=None, instagram_handle=None, updated_at=at)
        )
    return len(ids)


def export_rows(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each of the Contact's rows, by ``created_at``, then ``id``."""
    table = _R.__table__
    statement = (
        select(table)
        .where(table.c.contact_id == contact_id)
        .order_by(table.c.created_at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))

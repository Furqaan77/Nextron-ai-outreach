"""The ``ai_route_activations`` table's repository (FR-71, AD-3, AD-8): the only code that reads
or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks,
foreign key and partial unique index refuse a bad row (``IntegrityError``).

- ``active(route)``: the route's active activation (``deactivated_at`` is NULL) as a
  ``StoredActivation``, or ``None``;
- ``insert(...) -> id``: a new active activation, activated at ``at``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import AiRouteActivationRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredActivation:
    """One ``ai_route_activations`` row."""

    id: str
    route: str
    model: str
    effort: str
    prompt_version: str
    prompt_sha256: str
    schema_version: int
    actor: str
    bootstrap: bool
    test_run_id: str | None
    activated_at: datetime
    deactivated_at: datetime | None


_COLUMNS = (
    AiRouteActivationRecord.id,
    AiRouteActivationRecord.route,
    AiRouteActivationRecord.model,
    AiRouteActivationRecord.effort,
    AiRouteActivationRecord.prompt_version,
    AiRouteActivationRecord.prompt_sha256,
    AiRouteActivationRecord.schema_version,
    AiRouteActivationRecord.actor,
    AiRouteActivationRecord.bootstrap,
    AiRouteActivationRecord.test_run_id,
    AiRouteActivationRecord.activated_at,
    AiRouteActivationRecord.deactivated_at,
)


def active(route: str) -> StoredActivation | None:
    """The active activation of ``route``, or ``None`` when it has none."""
    statement = select(*_COLUMNS).where(
        AiRouteActivationRecord.route == str(route),
        AiRouteActivationRecord.deactivated_at.is_(None),
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredActivation(*row)


def insert(
    *,
    route: str,
    model: str,
    effort: str,
    prompt_version: str,
    prompt_sha256: str,
    schema_version: int,
    actor: str,
    bootstrap: bool,
    test_run_id: str | None,
    at: datetime,
) -> str:
    """Store a new active activation, activated at ``at``; return its ULID."""
    activation_id = new_id()
    current().session.execute(
        insert_rows(AiRouteActivationRecord).values(
            id=activation_id,
            route=route,
            model=model,
            effort=effort,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            schema_version=schema_version,
            actor=actor,
            bootstrap=bootstrap,
            test_run_id=test_run_id,
            activated_at=at,
            deactivated_at=None,
        )
    )
    return activation_id

"""The Channel Permission commands of a Contact (FR-24, FR-47, FR-41, AD-3, AD-4, AD-16, AD-17;
Story 10.7).

``ChannelPermissions(clock, research, compliance)`` owns ``channel_permissions``
(``nextron.db.repos.pipeline.channel_permissions``): the openings a prospect gave on WhatsApp or
Instagram, which Story 10.8's warm Drafts will need. It sends, fetches and schedules nothing,
and builds no link to either platform. Each method is the one ``@use_case`` a web handler
calls; it opens one transaction, and every write appends ``events`` rows (entity
``channel_permissions``, actor ``operator``, at the clock's now; IDs and codes only, no personal
data). A refusal raises ``PermissionRefused`` (``nextron.domain.channel_permission``) with each
refused field's message (``form`` for the whole form) and stores nothing; the table's CHECKs and
partial unique index repeat the rules.

Local times are read in the ``sender_timezone`` setting (``queries.pipeline.sender_zone``); while
it is unset or no longer parses, recording and updating are refused (``TIMEZONE_UNSET``).

- ``record(contact_id, channel, action, occurred_at, evidence, number, handle) -> id``: the
  fields are checked first (``check_record``: every problem at once); then an unknown or erased
  Contact is refused (``UNKNOWN_CONTACT``); then the contactability gate runs,
  ``compliance.assert_contactable(ContactSubject(contact_id), channel, follow_up)``, before any
  write, and a ``Blocked`` answer is refused with its reasons (``blocked_text``); then a second
  ``active`` permission on the channel is refused (``already_active``). Otherwise ``research``
  stores the Evidence as the Operator's Quote (``Research.record_operator_quote``) and one
  ``active`` row is inserted, ``last_inbound_at`` its ``occurred_at`` for Instagram (``NULL``
  for WhatsApp), with one event ``None`` to ``active``, cause ``permission_recorded``.
- ``update_inbound(contact_id, permission_id, last_inbound_at)``: the prospect messaged again on
  Instagram. A permission that isn't the Contact's is refused (``UNKNOWN_PERMISSION``), a
  WhatsApp one (``NO_WINDOW``) and a revoked one (``NOT_ACTIVE``) too; the time must be a local
  time not in the future and later than the stored one (``check_inbound``). The gate then runs
  as for ``record`` (channel ``instagram``); it moves ``last_inbound_at``, with one event
  ``active`` to ``active``, cause ``inbound_updated``.
- ``revoke(contact_id, permission_id)``: never gated. ``revoked`` (``revoked_at`` now), one
  event ``active`` to ``revoked``, cause ``permission_revoked``; an already revoked one is
  refused (``ALREADY_REVOKED``). No Draft table with a channel kind exists yet: Story 10.8
  makes this cancel the Contact's ``ready`` Drafts on the channel in the same transaction.
- ``opt_out(contact_id, permission_id) -> OptedOut``: "Opted out on this channel", never gated
  and allowed from any of the Contact's permissions. ``compliance.suppress(ContactSubject,
  person, operator, permission_id)`` suppresses every key known for the person, so every
  channel is blocked from then on; then every ``active`` permission of the Contact becomes
  ``revoked``, each with one event cause ``opted_out`` (``cause_ref`` the first suppression
  entry, when there is one). A person with no known key (no email address, profile URL or
  usable name key) gets no suppression entry (``written`` and ``existing`` both 0), and the
  permissions are still revoked; the page then says nothing was added to the Suppression List.
- ``view(contact_id) -> PermissionsView``: what the Channel Permission page reads, in the
  caller's (read-only) unit of work: the Contact's permissions, newest first
  (``queries.pipeline.permissions``), the ``sender_timezone`` zone (``None`` while unset) and
  the clock's now, for the Instagram window lines.
"""

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.db import eventlog
from nextron.db.repos.pipeline import channel_permissions as permissions_repo
from nextron.db.repos.pipeline.channel_permissions import StoredPermission
from nextron.db.uow import current, use_case
from nextron.domain.channel_permission import (
    ALREADY_REVOKED,
    CREATION,
    FORM_FIELD,
    NO_WINDOW,
    NOT_ACTIVE,
    UNKNOWN_PERMISSION,
    PermissionCause,
    PermissionChannel,
    PermissionEvent,
    PermissionRefused,
    PermissionStatus,
    already_active,
    blocked_text,
    check_inbound,
    check_record,
    transition,
)
from nextron.domain.contactability import (
    Blocked,
    ContactSubject,
    Purpose,
    SuppressionScope,
)
from nextron.domain.ids import is_ulid
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.clock import Clock

ENTITY: Final = "channel_permissions"
ACTOR: Final = "operator"
PURPOSE: Final = Purpose.FOLLOW_UP
UNKNOWN_CONTACT: Final = "This Contact no longer exists or was erased"
TIMEZONE_UNSET: Final = (
    "Set the sender's timezone on the Settings page first: times are read in it"
)


@dataclass(frozen=True, slots=True)
class OptedOut:
    """What "Opted out on this channel" did: the suppression entries written and already
    there, and the permissions it revoked."""

    written: int
    existing: int
    revoked: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PermissionsView:
    """The Contact's permissions, newest first, the sender's zone (``None`` while unset) and
    now."""

    permissions: tuple[StoredPermission, ...]
    zone: tzinfo | None
    now: datetime


def _zone() -> tzinfo:
    zone = pipeline_queries.sender_zone()
    if zone is None:
        raise PermissionRefused({FORM_FIELD: TIMEZONE_UNSET})
    return zone


def _gate(contact_id: str, channel: PermissionChannel) -> None:
    """Refuse a Contact the contactability gate blocks (AD-16)."""
    answer = compliance_api.assert_contactable(ContactSubject(contact_id), channel, PURPOSE)
    if isinstance(answer, Blocked):
        raise PermissionRefused({FORM_FIELD: blocked_text(answer.reasons)})


def _owned(contact_id: str, permission_id: str) -> StoredPermission:
    """The Contact's permission ``permission_id``; ``UNKNOWN_PERMISSION`` otherwise."""
    stored = permissions_repo.get(permission_id) if is_ulid(permission_id) else None
    if stored is None or stored.contact_id != contact_id:
        raise PermissionRefused({FORM_FIELD: UNKNOWN_PERMISSION})
    return stored


class ChannelPermissions:
    """The Channel Permission commands; see the module docstring."""

    def __init__(self, clock: Clock, research: Research, compliance: Compliance) -> None:
        self._clock = clock
        self._research = research
        self._compliance = compliance

    def view(self, contact_id: str) -> PermissionsView:
        """The Contact's permissions, the sender's zone and now; see the module docstring."""
        return PermissionsView(
            pipeline_queries.permissions(contact_id),
            pipeline_queries.sender_zone(),
            self._clock.now(),
        )

    @use_case
    def record(
        self,
        contact_id: str,
        channel: str,
        action: str,
        occurred_at: str,
        evidence: str,
        number: str,
        handle: str,
    ) -> str:
        """Record a new ``active`` permission; return its id. See the module docstring."""
        now = self._clock.now()
        with current().transaction():
            fields = check_record(
                channel, action, occurred_at, evidence, number, handle, now=now, tz=_zone()
            )
            contact = research_queries.contact(contact_id) if is_ulid(contact_id) else None
            if contact is None or contact.erased_at is not None:
                raise PermissionRefused({FORM_FIELD: UNKNOWN_CONTACT})
            _gate(contact.id, fields.channel)
            if permissions_repo.active_id(contact.id, fields.channel.value) is not None:
                raise PermissionRefused({FORM_FIELD: already_active(fields.channel)})
            evidence_id = self._research.record_operator_quote(contact.id, fields.evidence, now)
            inbound = fields.occurred_at if fields.channel is PermissionChannel.INSTAGRAM else None
            permission_id = permissions_repo.insert(
                contact_id=contact.id,
                channel=fields.channel.value,
                prospect_action=fields.action.value,
                occurred_at=fields.occurred_at,
                evidence_id=evidence_id,
                whatsapp_number=fields.whatsapp_number,
                instagram_handle=fields.instagram_handle,
                last_inbound_at=inbound,
                at=now,
            )
            eventlog.append(
                entity=ENTITY,
                entity_id=permission_id,
                from_status=None,
                to_status=CREATION.to.value,
                cause=CREATION.cause.value,
                cause_ref=evidence_id,
                actor=ACTOR,
                at=now,
            )
        return permission_id

    @use_case
    def update_inbound(self, contact_id: str, permission_id: str, last_inbound_at: str) -> None:
        """Move an Instagram permission's ``last_inbound_at``; see the module docstring."""
        now = self._clock.now()
        with current().transaction():
            stored = _owned(contact_id, permission_id)
            if stored.channel != PermissionChannel.INSTAGRAM.value:
                raise PermissionRefused({FORM_FIELD: NO_WINDOW})
            if stored.status != PermissionStatus.ACTIVE.value or stored.last_inbound_at is None:
                raise PermissionRefused({FORM_FIELD: NOT_ACTIVE})
            when = check_inbound(
                last_inbound_at, now=now, tz=_zone(), stored=stored.last_inbound_at
            )
            _gate(stored.contact_id, PermissionChannel.INSTAGRAM)
            permissions_repo.set_inbound(stored.id, when, now)
            eventlog.append(
                entity=ENTITY,
                entity_id=stored.id,
                from_status=stored.status,
                to_status=stored.status,
                cause=PermissionCause.INBOUND_UPDATED.value,
                cause_ref=None,
                actor=ACTOR,
                at=now,
            )

    @use_case
    def revoke(self, contact_id: str, permission_id: str) -> None:
        """Revoke an ``active`` permission; see the module docstring."""
        now = self._clock.now()
        with current().transaction():
            stored = _owned(contact_id, permission_id)
            if stored.status != PermissionStatus.ACTIVE.value:
                raise PermissionRefused({FORM_FIELD: ALREADY_REVOKED})
            _apply(stored, PermissionEvent.REVOKE, None, ACTOR, now)

    @use_case
    def opt_out(self, contact_id: str, permission_id: str) -> OptedOut:
        """Suppress the person and revoke every active permission; see the module docstring."""
        now = self._clock.now()
        with current().transaction():
            stored = _owned(contact_id, permission_id)
            result = self._compliance.suppress(
                ContactSubject(stored.contact_id),
                SuppressionScope.PERSON,
                SuppressionSource.OPERATOR,
                stored.id,
            )
            entries = result.written + result.existing
            cause_ref = entries[0] if entries else None
            revoked = []
            for permission in permissions_repo.for_contact(stored.contact_id):
                if permission.status == PermissionStatus.ACTIVE.value:
                    _apply(permission, PermissionEvent.OPT_OUT, cause_ref, ACTOR, now)
                    revoked.append(permission.id)
        return OptedOut(len(result.written), len(result.existing), tuple(revoked))


def _apply(
    stored: StoredPermission,
    event: PermissionEvent,
    cause_ref: str | None,
    actor: str,
    now: datetime,
) -> None:
    """Revoke ``stored`` through the machine and append its ``events`` row."""
    change = transition(PermissionStatus(stored.status), event)
    permissions_repo.revoke(stored.id, now)
    eventlog.append(
        entity=ENTITY,
        entity_id=stored.id,
        from_status=stored.status,
        to_status=change.to.value,
        cause=change.cause.value,
        cause_ref=cause_ref,
        actor=actor,
        at=now,
    )

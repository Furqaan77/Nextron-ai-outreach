"""Public command API of the ``sending`` service (AD-3, AD-4, AD-14, AD-21, FR-32).

Other services and the delivery layers change ``sending`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Sending(clock, *, env, compliance=None)`` owns the ``sending_domains``, ``sending_inboxes``
and ``outbound_messages`` tables, the SendingInbox machine (``nextron.domain.sending_inbox``) and
the OutboundMessage machine (``nextron.domain.outbound_message``). ``env`` is the engine's
environment: outside ``prod`` only allowlisted test inboxes can be registered (AD-14).
``compliance`` is the codec-bearing ``Compliance`` that issues unsubscribe tokens; ``enqueue``
raises ``TypeError`` without one (``main`` passes its own).

- ``@use_case register_inbox(address, label) -> RegisteredInbox(inbox_id, sending_domain_id,
  domain, domain_created)``:

  1. ``check_address`` and ``check_label`` run before any transaction (a non-``str`` argument
     raises ``TypeError``).
  2. Then one transaction, in order:

     - outside ``prod``, the ``sink_allowlist`` check (``allowlist_refusal``); an allowlist
       whose stored text no longer parses counts as empty, so the inbox is refused;
     - the duplicate-address check, over every status (a ``removed`` inbox's address stays
       registered);
     - ``from_name`` is ``queries.setup.get(Key.SENDER_NAME)``; ``SettingNotSet`` or
       ``InvalidSettingValue`` is ``sender_name_unset``;
     - the ``sending_domains`` row of the address's registrable domain is found or inserted
       (``domain_created``);
     - the inbox is inserted at ``CREATION.to`` (``connecting``), ``created_at`` = now;
     - one ``events`` row: entity ``sending_inboxes``, ``None`` to ``connecting``, cause
       ``registered``, no ``cause_ref``, actor ``operator``.

  3. Every refusal raises ``InboxRefused`` (``.refusal``; its message is
     ``REFUSAL_MESSAGES[refusal]``) and rolls the whole transaction back: nothing is stored.
  4. After it, the use case logs ``sending_inbox_registered`` (``inbox_id``,
     ``sending_domain_id`` and ``domain_created``) or ``sending_inbox_refused`` (``refusal``),
     never the address or the label.

- ``@use_case record_warmup(inbox_id, started_on, method, warm_level, tool_daily, ramp_step,
  ramp_target) -> WarmupRecord`` (Story 6.4, FR-33, FR-34): the warm-up form's six texts. One
  transaction, in order:

  1. ``sender_timezone`` through ``queries.setup.get``; ``SettingNotSet`` or
     ``InvalidSettingValue`` is ``timezone_unset``. Today is ``sending_policy.local_date`` of
     the clock's now in it;
  2. ``check_warmup(..., today=today)`` (``nextron.domain.sending_inbox``);
  3. the inbox's status: no such inbox (or an id that isn't a ULID) is ``unknown_inbox``, a
     ``removed`` one ``inbox_removed``;
  4. the six warm-up columns are updated (``repos.sending.inboxes.update_warmup``).

  It changes no status, so it appends no ``events`` row. Every refusal raises
  ``WarmupRefused`` (``.refusal``; the message names the field, never the value) and stores
  nothing. After it, the use case logs ``sending_warmup_recorded`` (``inbox_id``) or
  ``sending_warmup_refused`` (``refusal``, plus ``inbox_id`` when it is a ULID); never a form
  value.
- ``inbox_caps() -> InboxCaps(local_date, caps)``, a plain read for the current unit of work (a
  read-only one included): today's date in ``sender_timezone`` (``UTC`` while it is unset or
  unreadable) and ``queries.sending.inbox_caps`` on it, what the Sending Inboxes panel shows.

**OutboundMessages (Story 5.2, AD-6).** An approval's message waits as exactly one
OutboundMessage (OM) holding its own copy of the approved text; delivery (Epic 6) reads the OM,
never the Draft.

- ``@command enqueue(draft, approval) -> om_id`` joins the caller's unit of work (5.4's approve).
  ``draft`` is anything with an ``id`` (or the id itself); ``approval`` anything with
  ``approval_id``, ``draft_id``, ``rev`` and ``text_hash`` (``ApprovalRef``; Story 5.4 adds the
  ``approvals`` table). It re-reads the Draft by id (``LookupError`` when it, its revision, its
  footer snapshot, its Contact or Lead is unknown; ``ValueError`` when ``approval_id`` isn't a
  ULID). Then, before any write, it raises, dooming the whole transaction, when:

  1. the kind isn't ``step``/``recontact`` or there is no Enrollment (``UnsupportedKind``:
     Reply and rebook inboxes come from Threads, Story 6.7 and Epic 7);
  2. the Draft isn't ``approved``, or the approval names another Draft or a revision other than
     its ``current_rev`` (``NotApproved``);
  3. the Draft already has an OM that isn't ``cancelled`` (``AlreadyQueued``);
  4. ``compliance_api.assert_contactable(ContactSubject, "email", purpose)`` is ``Blocked``
     (``ContactBlocked``, ``.reasons``); purpose ``cold`` for Step 1, ``follow_up`` for a later
     Step, ``recontact`` for a ``recontact`` Draft (AD-16);
  5. the copy's ``render.text_hash`` differs from ``approval.text_hash`` (``HashMismatch``; a
     Contact without an email address too). The copy is ``render.compose(current revision, its
     footer snapshot, the Contact's current email)`` sent from the Enrollment's
     ``sending_inbox_id``.

  Then: a new OM id; ``compliance.issue_unsub_token`` for it and the copy's address; one
  ``queued`` row (``approved_hash`` the approval's ``text_hash``; snapshot: scoring's stored
  ``tier`` and Lead Score or NULL, the Enrollment's ``variant_id``, the Lead's ``segment_id``,
  the Contact's ``market_id``, the Draft's ``step_no``); one ``events`` row (entity
  ``outbound_messages``, ``None`` to ``queued``, cause ``enqueued``, ``cause_ref`` the approval
  id, actor ``system:sending``). It returns the OM id. The partial unique index and the insert
  trigger refuse a second live OM for one Draft whatever the caller.
- ``@command cancel(om_id) -> bool`` (un-approve, AD-6): a ``queued`` OM moves to ``cancelled``
  (conditional on ``status = 'queued'``) with ``cancelled_at`` and an ``events`` row (event
  ``cancel``, cause ``unapproved``, ``cause_ref`` the OM's ``approval_id``, actor
  ``system:sending``): ``True``. Already ``cancelled``: ``False``, no write. ``sending``, ``unknown`` or ``sent``: ``AlreadySending`` ("already
  sending"), unchanged. An unknown id: ``LookupError``.
- the module-level ``@command reparent_lead(n: LeadsMerged)`` (AD-3; ``main`` subscribes it):
  the merged Lead's OMs name the survivor (``lead_id`` is a reference, not part of the copy;
  the triggers allow it to change).
- ``ERASURE`` and the module-level ``@command erase_pii(n: ContactErased)`` (AD-17): every OM of
  the erased Contact gets ``to_address``, ``subject``, ``body`` and ``unsub_token`` ``[erased]``;
  ids, statuses, times, ``approved_hash`` and the snapshot stay. The contactability gate blocks
  an erased Contact's ``queued`` OM, and Story 5.4's cascade cancels it.

This service registers inboxes, records their warm-up and holds approved messages: the Gmail
connection, pause, resume and remove commands arrive with later Epic 6 stories, and enrollment
(Story 4.8) binds a Sequence to one of ``queries.sending.inboxes_for_binding()``. Nothing here
sends email (no ``MailPort``) or runs warm-up exchanges.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final, Protocol

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.sending import InboxCapView
from nextron.db import eventlog
from nextron.db.repos.sending import domains as domains_repo
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.repos.sending import outbound_messages as outbound_repo
from nextron.db.repos.sending.outbound_messages import NewOutboundMessage
from nextron.db.uow import command, current, use_case
from nextron.domain import outbound_message as om_machine
from nextron.domain import render
from nextron.domain.contactability import Blocked, BlockReason, ContactSubject, Purpose
from nextron.domain.draft import DraftKind, DraftStatus
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.notifications import ContactErased, LeadsMerged
from nextron.domain.outbound_message import OmEvent, OmKind, OmStatus
from nextron.domain.sending_inbox import (
    CREATION,
    InboxAddress,
    InboxRefused,
    InboxStatus,
    Refusal,
    WarmupRecord,
    WarmupRefusal,
    WarmupRefused,
    allowlist_refusal,
    check_address,
    check_label,
    check_warmup,
)
from nextron.domain.sending_policy import local_date
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.domain.sink import sink_applies
from nextron.ports.clock import Clock

ENTITY: Final = "sending_inboxes"
ACTOR: Final = "operator"
INBOX_REGISTERED: Final = "sending_inbox_registered"
INBOX_REFUSED: Final = "sending_inbox_refused"
WARMUP_RECORDED: Final = "sending_warmup_recorded"
WARMUP_REFUSED: Final = "sending_warmup_refused"
FALLBACK_TIMEZONE: Final = "UTC"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredInbox:
    """A registered inbox: its id, its Sending Domain's id and domain, and whether that Sending
    Domain was created for it."""

    inbox_id: str
    sending_domain_id: str
    domain: str
    domain_created: bool


@dataclass(frozen=True, slots=True)
class InboxCaps:
    """Today's date in ``sender_timezone`` and each listed inbox's caps on it."""

    local_date: date
    caps: tuple[InboxCapView, ...]


# OutboundMessages (Story 5.2) -------------------------------------------------------------------

OM_ENTITY: Final = "outbound_messages"
OM_ACTOR: Final = "system:sending"
EMAIL_CHANNEL: Final = "email"
ENQUEUE_KINDS: Final = (DraftKind.STEP, DraftKind.RECONTACT)
ALREADY_SENDING: Final = "already sending"


class OmRefused(DomainError, abstract=True):
    """An OutboundMessage command refused; nothing was written."""


class UnsupportedKind(OmRefused):
    """The Draft's kind isn't ``step`` or ``recontact``, or it has no Enrollment."""

    code = "om_unsupported_kind"


class NotApproved(OmRefused):
    """The Draft isn't ``approved``, or the approval isn't bound to its current revision."""

    code = "om_not_approved"


class AlreadyQueued(OmRefused):
    """The Draft already has an OutboundMessage that isn't ``cancelled``."""

    code = "om_already_queued"


class ContactBlocked(OmRefused):
    """The contactability gate (AD-16) blocks the Draft's Contact; ``reasons`` says why."""

    code = "om_contact_blocked"

    def __init__(self, reasons: tuple[BlockReason, ...]) -> None:
        super().__init__("the Contact may not be emailed: " + ", ".join(map(str, reasons)))
        self.reasons = reasons


class HashMismatch(OmRefused):
    """The message composed now isn't the text the approval is bound to."""

    code = "om_hash_mismatch"


class AlreadySending(OmRefused):
    """The OutboundMessage is past ``queued``: it can no longer be cancelled."""

    code = "om_already_sending"


class DraftRef(Protocol):
    """What ``enqueue`` reads of a Draft: its id (it re-reads the rest)."""

    @property
    def id(self) -> str: ...


class ApprovalRef(Protocol):
    """What ``enqueue`` reads of an approval (Story 5.4's ``approvals`` row)."""

    @property
    def approval_id(self) -> str: ...

    @property
    def draft_id(self) -> str: ...

    @property
    def rev(self) -> int: ...

    @property
    def text_hash(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _Addressed:
    """An OM's id and To address: what ``issue_unsub_token`` reads."""

    id: str
    to_address: str


def _purpose(kind: str, step_no: int | None) -> Purpose:
    """AD-16's purpose for a Step Draft: ``recontact``, else ``cold`` for Step 1 and
    ``follow_up`` for a later Step."""
    if kind == DraftKind.RECONTACT:
        return Purpose.RECONTACT
    return Purpose.COLD if step_no == 1 else Purpose.FOLLOW_UP


class Sending:
    """Sending Inboxes, their Sending Domains and OutboundMessages; see the module docstring."""

    def __init__(self, clock: Clock, *, env: str, compliance: Compliance | None = None) -> None:
        self._clock = clock
        self._env = env
        self._compliance = compliance

    # OutboundMessages (Story 5.2) ---------------------------------------------------------------

    @command
    def enqueue(self, draft: DraftRef | str, approval: ApprovalRef) -> str:
        """Hold the approved Draft as one ``queued`` OutboundMessage; see the module
        docstring."""
        compliance = self._compliance
        if compliance is None:
            raise TypeError("enqueue needs the codec-bearing Compliance")
        draft_id = draft if isinstance(draft, str) else draft.id
        if not is_ulid(approval.approval_id):
            raise ValueError("enqueue: approval.approval_id must be a ULID")
        found = drafting_queries.draft(draft_id)
        if found is None:
            raise LookupError("enqueue: the Draft is unknown")
        if found.kind not in ENQUEUE_KINDS or found.enrollment_id is None:
            raise UnsupportedKind(f"enqueue: a {found.kind} Draft has no bound inbox")
        enrollment = sequencing_queries.enrollment(found.enrollment_id)
        if enrollment is None:
            raise UnsupportedKind("enqueue: the Draft's Enrollment is unknown")
        if (
            found.status != DraftStatus.APPROVED
            or approval.draft_id != found.id
            or approval.rev != found.current_rev
        ):
            raise NotApproved("enqueue: the Draft isn't approved at this revision")
        if outbound_repo.live_of_draft(found.id) is not None:
            raise AlreadyQueued("enqueue: the Draft already has a live OutboundMessage")
        verdict = compliance_api.assert_contactable(
            ContactSubject(enrollment.contact_id),
            EMAIL_CHANNEL,
            _purpose(found.kind, found.step_no),
        )
        if isinstance(verdict, Blocked):
            raise ContactBlocked(verdict.reasons)
        revision = drafting_queries.current_revision(found)
        if revision is None:
            raise LookupError("enqueue: the Draft's revision is unknown")
        snapshot = drafting_queries.footer_snapshot(revision.footer_snapshot_id)
        if snapshot is None:
            raise LookupError("enqueue: the revision's footer snapshot is unknown")
        contact = research_queries.contact(enrollment.contact_id)
        if contact is None:
            raise LookupError("enqueue: the Draft's Contact is unknown")
        if contact.email is None:
            raise HashMismatch("enqueue: the Contact has no email address")
        lead = discovery_queries.lead(enrollment.lead_id)
        if lead is None:
            raise LookupError("enqueue: the Draft's Lead is unknown")
        copy = render.compose(revision, snapshot, contact.email)
        digest = render.text_hash(
            copy.to_address, enrollment.sending_inbox_id, copy.subject, copy.body
        )
        if digest != approval.text_hash:
            raise HashMismatch("enqueue: the message changed since it was approved")
        om_id = outbound_repo.new_om_id()
        token = compliance.issue_unsub_token(_Addressed(om_id, copy.to_address))
        score = scoring_queries.stored(lead.id)
        now = self._clock.now()
        outbound_repo.insert(
            om_id,
            NewOutboundMessage(
                draft_id=found.id,
                approval_id=approval.approval_id,
                kind=str(OmKind(found.kind)),
                to_address=copy.to_address,
                inbox_id=enrollment.sending_inbox_id,
                contact_id=contact.id,
                lead_id=lead.id,
                enrollment_id=enrollment.id,
                subject=copy.subject,
                body=copy.body,
                approved_hash=approval.text_hash,
                unsub_token=token,
                tier=None if score is None or score.tier is None else str(score.tier),
                lead_score=None if score is None else score.lead,
                variant_id=enrollment.variant_id,
                segment_id=lead.segment_id,
                market_id=contact.market_id,
                step_no=found.step_no,
            ),
            status=om_machine.CREATION.to,
            created_at=now,
        )
        eventlog.append(
            entity=OM_ENTITY,
            entity_id=om_id,
            from_status=None,
            to_status=om_machine.CREATION.to,
            cause=om_machine.CREATION.cause,
            cause_ref=approval.approval_id,
            actor=OM_ACTOR,
            at=now,
        )
        return om_id

    @command
    def cancel(self, om_id: str) -> bool:
        """Cancel a ``queued`` OutboundMessage (un-approve); see the module docstring."""
        found = sending_queries.outbound_message(om_id)
        if found is None:
            raise LookupError("cancel: the OutboundMessage is unknown")
        if found.status == OmStatus.CANCELLED:
            return False
        if found.status != OmStatus.QUEUED:
            raise AlreadySending(ALREADY_SENDING)
        moved = om_machine.transition(found.status, OmEvent.CANCEL)
        now = self._clock.now()
        if not outbound_repo.set_status(found.id, found.status, moved.to, now):
            raise AlreadySending(ALREADY_SENDING)
        eventlog.append(
            entity=OM_ENTITY,
            entity_id=found.id,
            from_status=found.status,
            to_status=moved.to,
            cause=moved.cause,
            cause_ref=found.approval_id,
            actor=OM_ACTOR,
            at=now,
        )
        return True

    # Sending Inboxes (Story 4.7) ----------------------------------------------------------------

    @use_case
    def register_inbox(self, address: str, label: str) -> RegisteredInbox:
        """Register a Sending Inbox at ``connecting``; see the module docstring."""
        try:
            checked = check_address(address)
            shown_label = check_label(label)
            with current().transaction():
                registered = self._register(checked, shown_label)
        except InboxRefused as refused:
            log.info(INBOX_REFUSED, extra={"refusal": str(refused.refusal)})
            raise
        log.info(
            INBOX_REGISTERED,
            extra={
                "inbox_id": registered.inbox_id,
                "sending_domain_id": registered.sending_domain_id,
                "domain_created": registered.domain_created,
            },
        )
        return registered

    def _register(self, checked: InboxAddress, label: str) -> RegisteredInbox:
        allowlist = self._allowlist() if sink_applies(self._env) else ()
        refusal = allowlist_refusal(checked.address, self._env, allowlist)
        if refusal is not None:
            raise InboxRefused(refusal)
        if inboxes_repo.id_by_address(checked.address) is not None:
            raise InboxRefused(Refusal.ALREADY_REGISTERED)
        try:
            from_name = str(setup_queries.get(Key.SENDER_NAME))
        except (SettingNotSet, InvalidSettingValue):
            raise InboxRefused(Refusal.SENDER_NAME_UNSET) from None
        now = self._clock.now()
        found = domains_repo.by_domain(checked.domain)
        if found is None:
            domain_id = domains_repo.insert(domain=checked.domain, created_at=now)
        else:
            domain_id = found.id
        inbox_id = inboxes_repo.insert(
            address=checked.address,
            label=label,
            sending_domain_id=domain_id,
            from_name=from_name,
            status=CREATION.to,
            created_at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=None,
            to_status=CREATION.to,
            cause=CREATION.cause,
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        return RegisteredInbox(inbox_id, domain_id, checked.domain, found is None)

    @staticmethod
    def _allowlist() -> tuple[object, ...]:
        """The ``sink_allowlist`` entries; a stored list that no longer parses counts as
        empty."""
        try:
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))
        except InvalidSettingValue:
            return ()

    # Warm-up (Story 6.4) ----------------------------------------------------------------------

    @use_case
    def record_warmup(
        self,
        inbox_id: str,
        started_on: str,
        method: str,
        warm_level: str,
        tool_daily: str,
        ramp_step: str,
        ramp_target: str,
    ) -> WarmupRecord:
        """Record an inbox's warm-up and ramp; see the module docstring."""
        try:
            with current().transaction():
                record = self._record_warmup(
                    inbox_id, started_on, method, warm_level, tool_daily, ramp_step, ramp_target
                )
        except WarmupRefused as refused:
            extra: dict[str, object] = {"refusal": str(refused.refusal)}
            if isinstance(inbox_id, str) and is_ulid(inbox_id):
                extra["inbox_id"] = inbox_id
            log.info(WARMUP_REFUSED, extra=extra)
            raise
        log.info(WARMUP_RECORDED, extra={"inbox_id": inbox_id})
        return record

    def _record_warmup(
        self,
        inbox_id: str,
        started_on: str,
        method: str,
        warm_level: str,
        tool_daily: str,
        ramp_step: str,
        ramp_target: str,
    ) -> WarmupRecord:
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except (SettingNotSet, InvalidSettingValue):
            raise WarmupRefused(WarmupRefusal.TIMEZONE_UNSET) from None
        today = local_date(self._clock.now(), timezone)
        record = check_warmup(
            started_on, method, warm_level, tool_daily, ramp_step, ramp_target, today=today
        )
        if not isinstance(inbox_id, str) or not is_ulid(inbox_id):
            raise WarmupRefused(WarmupRefusal.UNKNOWN_INBOX)
        status = inboxes_repo.status_of(inbox_id)
        if status is None:
            raise WarmupRefused(WarmupRefusal.UNKNOWN_INBOX)
        if status == InboxStatus.REMOVED:
            raise WarmupRefused(WarmupRefusal.INBOX_REMOVED)
        inboxes_repo.update_warmup(
            inbox_id,
            warmup_started_on=record.warmup_started_on,
            warmup_method=record.warmup_method,
            warm_level=record.warm_level,
            warmup_tool_daily=record.warmup_tool_daily,
            ramp_step=record.ramp_step,
            ramp_target=record.ramp_target,
        )
        return record

    def inbox_caps(self) -> InboxCaps:
        """Today's date in ``sender_timezone`` and each listed inbox's caps on it, read in the
        current unit of work (a read-only one included)."""
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except (SettingNotSet, InvalidSettingValue):
            timezone = FALLBACK_TIMEZONE
        today = local_date(self._clock.now(), timezone)
        return InboxCaps(today, sending_queries.inbox_caps(today))


# Erasure (Story 5.2) ----------------------------------------------------------------------------

# What sending's erase_pii does to each of its tables with a personal-data column.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "outbound_messages": TableErasure(
            Coverage.TOMBSTONED,
            "the recipient, subject, body and unsubscribe token of every OutboundMessage of the "
            "Contact become [erased]; ids, statuses, times, the approved hash and the snapshot "
            "stay (Story 5.2)",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``sending``'s ``ContactErased`` subscriber (AD-17, Story 5.2): tombstone the copy of every
    OutboundMessage of the Contact (``ERASURE``)."""
    outbound_repo.tombstone_contact(n.contact_id)


@command
def reparent_lead(n: LeadsMerged) -> None:
    """``sending``'s ``LeadsMerged`` subscriber (AD-3, Story 5.2): move the merged Lead's
    OutboundMessages to the survivor; the copy and the snapshot stay."""
    outbound_repo.move_lead(n.merged_lead_id, n.survivor_lead_id)

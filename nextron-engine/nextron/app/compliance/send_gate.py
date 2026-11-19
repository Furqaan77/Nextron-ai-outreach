"""The closed send gate's reads and its one alert (FR-37, FR-18, FR-23, FR-33, FR-34, FR-50,
FR-64, AD-11, AD-16, NFR-7; Story 6.9), re-exported from ``nextron.app.compliance.api``.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, the ``nextron.app.queries``
modules and ``nextron.app.compliance.api`` only: no repository, and no ``app.sequencing``,
``app.approval``, ``app.drafting`` or ``app.sending`` (c6).

**``assert_sendable(om: SendCandidate, now) -> GateResult``** gathers every fact
``nextron.domain.send_gate.evaluate`` judges and returns its result: every failing predicate, in
``PREDICATES`` order. It only reads (through ``nextron.app.queries.*``, ``assert_contactable``
and ``live_eligibility``), so it runs in a read-only unit of work (the "Approved, not yet sent"
list, Epic 5) and inside 6.10's claim transaction alike; it writes, caches and logs nothing.
``om`` must be a ``SendCandidate`` (otherwise ``TypeError``) and ``now`` aware (otherwise
``ValueError``). Days are calendar days in ``sender_timezone``
(``sending_policy.local_date``).

The facts, each read on every call:

- ``contactable``: ``compliance_api.assert_contactable(ContactSubject(om.contact_id), "email",
  purpose)``, looked up at call time (the AD-16 contract test replaces it with a recording
  fake). The purpose comes from the kind: ``step`` with Step 1 (or a Draft that can't be read)
  is ``cold``, a later Step ``follow_up``; then ``recontact``, ``reply`` and ``rebook``.
- ``approval_live``: the Draft (``queries.drafting.draft``) is ``approved``, and
  ``queries.approval.current_approval(draft_id)`` is the OM's ``approval_id`` for that Draft;
  ``approved_at`` comes from it.
- ``hash_matches``: the approved hash (only when the approval names the Draft's current
  revision), the current revision's stored ``text_hash``, and a fresh recompute: ``render.compose``
  of the revision, its stored footer snapshot and the Contact's current email, hashed with the
  OM's inbox (``render.text_hash``, as ``drafting``'s ``_prepare`` does).
- ``inbox_ready``: the OM's inbox among ``queries.sending.inboxes()`` (a ``removed`` one is
  missing), its status and address, ``queries.sending.domain_auth(domain, now)`` (``None`` or a
  blocked verdict fails) and ``queries.ops.recovery_mode()``.
- ``footer_complete``: ``render.footer_complete(queries.setup.footer_settings(), the Contact's
  current Market)``, and whether the revision's snapshot is for that Market and its text equals
  ``render.footer(current settings, that Market, the Contact's Provenance source)``. An approved
  Draft whose Contact moved Market, or whose footer settings changed, is blocked until it is
  approved again.
- ``market_eligible``: the Contact's current Market (``queries.setup.market_by_id``) and, for a
  cold kind, ``live_eligibility(contact_id, now).eligible``, also looked up at call time.
- ``enrollment_active``: ``queries.sequencing.enrollment(om.enrollment_id)``'s status, only when
  that Enrollment is the OM's Contact's and binds the OM's inbox.
- ``thread_quiet``: ``queries.replies.thread_quiet(om.thread_id, approved_at)``.
- ``campaign_active``: ``queries.setup.campaign_active`` of the Enrollment's Campaign.
- ``in_send_window``: the Contact's ``tz``, read as unknown (``None``) when its ``tz_basis`` is
  ``us_unknown_fallback`` or ``no_market``, with its Market.
- ``under_daily_cap``: ``queries.sending.cold_sent_on(inbox, local_date)`` against the inbox's
  ``cold_daily_caps(local_date)`` entry.
- ``warmup_ok``: ``sending_policy.warmup_days(inbox.plan, local_date)``.
- ``under_total_ceiling``: ``queries.sending.sent_total_on(inbox, local_date)`` plus
  ``tool_send_figure(inbox.plan)`` against ``queries.sending.ceiling()``.

Gathering a fact catches only ``LookupError``, ``ValueError`` and ``DomainError`` (a missing or
invalid row, an unset or unreadable setting, a stored definition that no longer decodes) and
turns it into ``None``, which fails the predicate it feeds. A database error propagates, so 6.10's
claim fails with the OM still ``queued``. The inbox list, ``warmup_days`` and
``tool_send_figure`` are gathered the same way, so a bad inbox row or warm-up plan fails its
predicates instead of raising. Story 6.9's seams (``current_approval``,
``thread_quiet``, ``cold_sent_on``, ``sent_total_on``) answer ``None`` until Stories 5.4, 6.8 and
6.10 fill them, so nothing can pass the gate yet.

**``SendGate(clock, notify)``**, for 6.10's claim unit: ``@command check(om) ->
GateResult`` runs ``assert_sendable(om, clock.now())`` and, when ``hash_matches`` fails while
the OM's approval is its Draft's current one (``queries.approval.current_approval(om.draft_id)``
has the OM's ``approval_id`` and ``draft_id``), raises the ops alert ``send_hash_mismatch``
(subject the OM id; text holding the OM id only; Telegram and the dashboard) through
``notify.alert``, which keeps one per OM. With no current approval (always, until Story 5.4)
there is no approved text to mismatch, so no alert. It writes nothing else: the gate never
cancels an OM and never writes another owner's rows.
"""

from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Final, Protocol

from nextron.app.queries import approval as approval_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import replies as replies_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import command
from nextron.domain import render
from nextron.domain.alert import Channel
from nextron.domain.contact_market import TzBasis
from nextron.domain.contactability import Allowed, ContactSubject, Purpose
from nextron.domain.contacts import ProvenanceSubject
from nextron.domain.draft import DraftStatus
from nextron.domain.errors import DomainError
from nextron.domain.send_gate import (
    GateFacts,
    GateResult,
    OmKind,
    Predicate,
    SendCandidate,
    evaluate,
)
from nextron.domain.sending_policy import local_date, tool_send_figure, warmup_days
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock

SEND_HASH_MISMATCH: Final = "send_hash_mismatch"  # the ops alert's kind
ALERT_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)  # every channel, as ops alerts
EMAIL: Final = "email"
UNKNOWN_TZ_BASES: Final = frozenset({TzBasis.US_UNKNOWN_FALLBACK, TzBasis.NO_MARKET})
FIRST_STEP: Final = 1
_FACT_ERRORS: Final = (LookupError, ValueError, DomainError)


def hash_mismatch_text(om_id: str) -> str:
    """The ``send_hash_mismatch`` alert's text: the OM id only."""
    return (
        f"Send blocked: message {om_id} no longer matches its approved text. "
        "Approve its Draft again."
    )


def _fact[T](read: Callable[[], T]) -> T | None:
    """``read()``, or ``None`` for a missing or invalid fact (fail closed)."""
    try:
        return read()
    except _FACT_ERRORS:
        return None


def _purpose(kind: OmKind, step_no: int | None) -> Purpose:
    if kind is OmKind.STEP:
        return Purpose.FOLLOW_UP if step_no is not None and step_no > FIRST_STEP else Purpose.COLD
    return Purpose(str(kind))


class _Text(Protocol):
    subject: str
    body: str


class _Snapshot(Protocol):
    market_id: str
    text: str


def _recomputed(
    revision: _Text | None, snapshot: _Snapshot | None, email: str | None, inbox_id: str
) -> str | None:
    if revision is None or snapshot is None or not email:
        return None
    composed = render.compose(revision, snapshot, email)
    return render.text_hash(composed.to_address, inbox_id, composed.subject, composed.body)


def _source(contact_id: str) -> str | None:
    return next(
        (
            record.source_kind
            for record in research_queries.provenance(contact_id)
            if record.subject == ProvenanceSubject.CONTACT
        ),
        None,
    )


def _sender_date(now: datetime) -> date | None:
    return _fact(lambda: local_date(now, str(setup_queries.get(Key.SENDER_TIMEZONE))))


def assert_sendable(om: SendCandidate, now: datetime) -> GateResult:
    """Every predicate the OM fails at ``now``; see the module docstring."""
    if not isinstance(om, SendCandidate):
        raise TypeError(f"assert_sendable takes a SendCandidate, not {type(om).__name__}")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    cold = om.cold

    draft = _fact(lambda: drafting_queries.draft(om.draft_id))
    contact = _fact(lambda: research_queries.contact(om.contact_id))
    purpose = _purpose(om.kind, None if draft is None else draft.step_no)

    # the contactability gate first, a global of api looked up at call time (AD-16)
    answer = _fact(
        lambda: compliance_api.assert_contactable(ContactSubject(om.contact_id), EMAIL, purpose)
    )
    eligible = None
    if cold:
        decision = _fact(lambda: compliance_api.live_eligibility(om.contact_id, now))
        eligible = None if decision is None else decision.eligible

    # the approval and the text
    approval = _fact(lambda: approval_queries.current_approval(om.draft_id))
    revision = None if draft is None else _fact(lambda: drafting_queries.current_revision(draft))
    snapshot = (
        None
        if revision is None
        else _fact(lambda: drafting_queries.footer_snapshot(revision.footer_snapshot_id))
    )
    approval_current = (
        draft is not None
        and draft.status is DraftStatus.APPROVED
        and approval is not None
        and approval.id == om.approval_id
        and approval.draft_id == om.draft_id
    )
    approved_hash = (
        approval.approved_hash
        if approval is not None and draft is not None and approval.rev == draft.current_rev
        else None
    )
    email = None if contact is None or contact.erased_at is not None else contact.email
    recomputed = _fact(lambda: _recomputed(revision, snapshot, email, om.sending_inbox_id))

    # the inbox
    inboxes = _fact(sending_queries.inboxes) or ()
    inbox = next((found for found in inboxes if found.id == om.sending_inbox_id), None)
    auth = (
        None
        if inbox is None
        else _fact(lambda: sending_queries.domain_auth(inbox.sending_domain_id, now))
    )

    # the Market and the footer
    seeded = (
        None
        if contact is None or contact.market_id is None
        else _fact(lambda: setup_queries.market_by_id(str(contact.market_id)))
    )
    market = None if seeded is None else seeded.definition
    settings = _fact(setup_queries.footer_settings)
    footer_missing = None
    footer_matches = None
    if seeded is not None and settings is not None:
        footer_missing = _fact(lambda: render.footer_complete(settings, seeded.definition).missing)
        expected = _fact(
            lambda: render.footer(settings, seeded.definition, _source(om.contact_id))
        )
        footer_matches = (
            snapshot is not None
            and expected is not None
            and snapshot.market_id == seeded.id
            and snapshot.text == expected
        )

    # the cold facts
    enrollment_status = None
    campaign = None
    thread = None
    contact_tz = None
    cold_sent = cold_cap = days = total = ceiling = None
    if cold:
        enrollment = (
            None
            if om.enrollment_id is None
            else _fact(lambda: sequencing_queries.enrollment(str(om.enrollment_id)))
        )
        if (
            enrollment is not None
            and enrollment.contact_id == om.contact_id
            and enrollment.sending_inbox_id == om.sending_inbox_id
        ):
            enrollment_status = enrollment.status
            campaign = _fact(lambda: setup_queries.campaign_active(enrollment.campaign_id))
        if approval is not None:
            thread = _fact(lambda: replies_queries.thread_quiet(om.thread_id, approval.approved_at))
        if contact is not None and contact.tz_basis not in UNKNOWN_TZ_BASES:
            contact_tz = contact.tz
        day = _sender_date(now)
        if day is not None and inbox is not None:
            cold_sent = _fact(lambda: sending_queries.cold_sent_on(inbox.id, day))
            cold_cap = _fact(lambda: sending_queries.cold_daily_caps(day).get(inbox.id))
            days = _fact(lambda: warmup_days(inbox.plan, day))
            sent = _fact(lambda: sending_queries.sent_total_on(inbox.id, day))
            tool = _fact(lambda: tool_send_figure(inbox.plan))
            total = None if sent is None or tool is None else sent + tool
            ceiling = _fact(sending_queries.ceiling)

    facts = GateFacts(
        contactable=None if answer is None else isinstance(answer, Allowed),
        approval_current=approval_current,
        approved_at=None if approval is None else approval.approved_at,
        approved_hash=approved_hash,
        revision_hash=None if revision is None else revision.text_hash,
        recomputed_hash=recomputed,
        inbox_status=None if inbox is None else str(inbox.status),
        inbox_address=None if inbox is None else inbox.address,
        domain_auth_ok=None if auth is None else not auth.verdict.blocked,
        recovery_mode=_fact(ops_queries.recovery_mode),
        footer_missing=footer_missing,
        footer_matches=footer_matches,
        market=market,
        eligible=eligible,
        enrollment_status=enrollment_status,
        thread_quiet=thread,
        campaign_active=campaign,
        contact_tz=contact_tz,
        cold_sent_today=cold_sent,
        cold_cap=cold_cap,
        warmup_days=days,
        sent_total_today=total,
        ceiling=ceiling,
    )
    return evaluate(om.kind, facts, now)


class Alerts(Protocol):
    """What the gate needs of the ``notify`` service."""

    def alert(self, kind: str, subject: str, text: str, channels: Iterable[Channel]) -> object: ...


def _approval_is_current(om: SendCandidate) -> bool:
    """Whether the OM's approval is its Draft's current one (a real mismatch, not no approval)."""
    approval = _fact(lambda: approval_queries.current_approval(om.draft_id))
    return (
        approval is not None and approval.id == om.approval_id and approval.draft_id == om.draft_id
    )


class SendGate:
    """The gate as 6.10's claim unit runs it; see the module docstring."""

    def __init__(self, clock: Clock, notify: Alerts) -> None:
        self._clock = clock
        self._notify = notify

    @command
    def check(self, om: SendCandidate) -> GateResult:
        """``assert_sendable(om, now)``, alerting a ``hash_matches`` failure once per OM."""
        result = assert_sendable(om, self._clock.now())
        if Predicate.HASH_MATCHES in result.failing and _approval_is_current(om):
            self._notify.alert(
                SEND_HASH_MISMATCH, om.om_id, hash_mismatch_text(om.om_id), ALERT_CHANNELS
            )
        return result


# api re-exports this module's names, so it is imported last: either module may load first.
from nextron.app.compliance import api as compliance_api  # noqa: E402

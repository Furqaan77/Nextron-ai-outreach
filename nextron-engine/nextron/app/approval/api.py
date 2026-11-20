"""Public command API of the ``approval`` service (AD-3, AD-4, AD-6, AD-16, AD-21; Stories 4.9,
5.4).

Other services and the delivery layers change ``approval`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Approval(clock, *, sending=None)`` owns the ``drafts`` status group
(``nextron.db.repos.approval.drafts``), the ``approvals`` and ``approval_sessions`` tables, and
applies the Draft machine (``nextron.domain.draft``). Every status change sets ``status_at`` and
appends one ``events`` row in the same transaction: entity ``drafts``, at the clock's now, actor
``system:approval`` for a cascade and ``operator`` for an approval. ``sending`` is the
codec-bearing ``Sending`` whose ``enqueue`` holds an approved message; only ``approve`` needs it
(``TypeError`` without one), so the cascades keep working on a bare ``Approval(clock)``.

**Approve (Story 5.4, AD-6).** ``@use_case approve(draft_id, rev, shown_hash, *, served_at=None,
first_served_at=None) -> ApproveResult(outcome, draft_id, reasons, approval_id, om_id,
queue_clear)``. One transaction (``BEGIN IMMEDIATE``). First every check, reads only, in this
order; the first failure decides, and a refusal writes nothing at all:

1. an unknown Draft (or an id that isn't a ULID): ``UnknownDraft`` (a ``LookupError``; the page
   answers 404, and only for it);
2. the Draft isn't ``ready``: ``blocked`` "Already approved" (``approved``) or "This Draft is no
   longer in the queue" (any other status);
3. ``rev`` isn't its ``current_rev``: ``stale`` ("This Draft changed since you opened it"); a
   ``ready`` Draft without a revision is ``blocked`` "This Draft can't be approved yet";
4. the grounding result of that revision isn't ``pass``: ``blocked`` "Grounding check failed"
   (``fail``) or "Grounding check pending" (``pending``, or no result yet);
5. the card's footer check fails (``queries.approval.card_of(draft_id).blocked``, the notice
   Story 5.3 shows): ``blocked`` with that notice;
6. ``compliance_api.assert_contactable(ContactSubject, "email", purpose)``, purpose
   ``domain.draft.contact_purpose(kind, step_no)``, is ``Blocked``: ``blocked`` "Contact is
   suppressed" plus each reason code (a Contact the gate can't look up counts as unbindable);
7. the Draft can't be bound: a kind other than ``step``/``recontact`` (``BINDABLE_KINDS``; reply
   and rebook wait for Epic 7, decision 1), no Enrollment, or no ``shown_hash`` (no message or
   no address): ``blocked`` "This Draft can't be approved yet";
8. ``shown_hash`` isn't the card's ``shown_hash`` now (the hash of the message composed from the
   same inputs as the card, the Contact's current address included): ``stale``.

Then, in the same transaction: one ``approvals`` row (``text_hash`` the verified hash,
``approved_by`` ``operator``, ``approved_at`` now, ``review_ms``, ``edit_ratio``); the Draft
``ready → approved`` (conditional) with its ``events`` row (cause ``approved``, ``cause_ref`` the
approval id, actor ``operator``); ``sending.enqueue(draft, approval)``. If ``enqueue`` (or the
conditional move) fails, the whole unit rolls back (no approval, the Draft stays ``ready``, no
OM, no session change) and the result is ``blocked`` with the error's message. The use case logs
``draft_approved`` (``draft_id``, ``approval_id``, ``om_id``) or ``draft_approve_refused``
(``draft_id``, ``outcome``), never text.

- **Metrics.** ``review_ms`` is the whole milliseconds from ``served_at`` (when this dashboard
  session last served the Draft's card; the web layer's in-memory stamp) to now, never below 0;
  ``None`` without a stamp. ``edit_ratio`` is ``domain.render.edit_ratio(revision 1, the
  approved revision)``.
- **Sessions.** Read before the approval is written: the open session and its last activity (the
  newest ``approved_at`` since it started, else its ``started_at``). After the approval: an open
  session idle for longer than ``approval_session_idle`` (``queries.setup``; ``PT15M`` when unset
  or unreadable) is closed at its last activity; with none open, one opens at
  ``first_served_at`` (the earliest card this dashboard session served) when that is not before
  the newest closed session's end, else at ``served_at`` (this card's serve) when that is not
  before it, else now; never later than now. The decision adds 1 to ``decisions`` and
  ``review_ms`` capped at the idle setting (0 without one) to ``active_ms`` (the ``approvals``
  row keeps the raw ``review_ms``). Then, when the due queue (``queries.approval.due_queue``
  with the live Eligibility of ``Compliance.evaluate_eligibility``) has no card left, the
  session closes at now, or at its start if the clock moved back (``queue_clear`` true).

**Edit then approve (Story 5.6, AD-6).** ``Approval(clock, *, sending=None, drafting=None)``:
``drafting`` is the queue-bearing ``Drafting`` whose ``revise`` stores an edit (``edit`` raises
``TypeError`` without it). The Draft's pending approval lives in its ``pending_approval_rev``,
``pending_approval_hash`` and ``pending_review_ms`` (``nextron.db.repos.approval.drafts``);
only this service writes them: ``edit`` sets them; ``approve``, ``complete_pending`` and the
cascades clear them for each Draft they move.

- ``@use_case edit(draft_id, rev, shown_hash, subject, body, *, served_at=None,
  first_served_at=None) -> ApproveResult``. One transaction; every refusal writes nothing.
  Checks, in order: an unknown Draft raises ``UnknownDraft``; a status other than ``ready`` or
  ``approved`` is ``blocked`` "This Draft is no longer in the queue"; ``rev`` not the current
  one is ``stale``; a card without a ``shown_hash`` is ``blocked`` "This Draft can't be approved
  yet"; ``shown_hash`` not the card's is ``stale``; a stripped subject and a body equal to the
  current revision's are ``blocked`` "Nothing changed". For an ``approved`` Draft its live OM
  must be ``queued`` and is cancelled (``sending.api.cancel_for_cascade``), else ``blocked``
  "Already sending" (nothing written); then the Draft moves ``approved -> ready`` (cause
  ``edited``, actor ``operator``, ``cause_ref`` its approval). Then ``drafting.revise`` stores
  the new revision (origin ``operator_edit``, its ``check_grounding`` job on the interactive
  lane); a ``ValueError`` (the text rules) rolls everything back as ``blocked`` with its message
  (first letter upper-cased), a ``LookupError`` as ``blocked`` "This Draft can't be approved
  yet". The pending approval records the new ``rev``, the new card's ``shown_hash`` and
  ``review_ms``; the decision counts in the session as in ``approve``. The result is
  ``pending`` "Recheck pending".
- ``@use_case complete_pending(draft_id, rev) -> ApproveResult | None``, the
  ``complete_pending_approval`` job's use case (``drafting`` enqueues it once the grounding
  result of the pending revision is recorded). ``None``, writing nothing, when the Draft has no
  pending approval for ``rev``. Otherwise ``_check(draft_id, rev, pending_approval_hash)``, the
  very checks ``approve`` runs: all pass -> the approval is recorded exactly as ``approve`` does
  (``review_ms`` the pending one, no session change) and the pending fields are cleared;
  ``stale`` -> nothing written; ``blocked`` -> the pending fields are cleared and nothing else
  (a grounding result still ``pending`` writes nothing). A failed enqueue rolls the unit back and
  raises ``PendingApprovalFailed``, so the job retries.

Nothing here sends anything or touches ``MailPort``.

**Cascades (Story 4.9; Story 5.4 adds ``approved`` Drafts).**

- ``@command cancel_pending(enrollment_ids) -> tuple[str, ...]``: each ``ready`` Draft of those
  Enrollments moves to ``cancelled`` (event ``cancel``, cause ``sequence_stopped``, ``cause_ref``
  its Enrollment, actor ``system:approval``). Then each ``approved`` one: its live OM, when
  ``queued``, is cancelled through ``sending.api.cancel_for_cascade`` and the Draft moves
  ``approved → cancelled`` the same way; with no live OM it just moves. An OM ``sent`` leaves
  the Draft ``approved`` silently (nothing to un-approve). An OM ``sending`` or ``unknown`` (or a cancel that lost the race) leaves the Draft ``approved`` and logs
  ``approval_cascade_in_flight`` (``draft_id``, ``om_id``); nothing raises. It returns the ids
  cancelled: the ``ready`` ones, then the ``approved`` ones, each by Enrollment, then Step.
  ``enrollment_ids`` must be a ``tuple`` or ``list`` of ``str`` (``TypeError`` otherwise); a
  repeat is a no-op. ``Sequencing.stop`` and its suppression sweep call it for what they
  stopped.
- ``@command cancel_pending_suppressed(n: SubjectSuppressed)``: the ``SubjectSuppressed``
  backstop, registered after ``Sequencing.stop_suppressed``: the same for every ``ready`` or
  ``approved`` Draft whose Enrollment has ended (``stopped`` or ``completed``). Idempotent.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.drafting.api import Drafting
from nextron.app.queries import approval as approval_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.approval import BINDABLE_KINDS, QueueCard
from nextron.app.sending import api as sending_api
from nextron.app.sending.api import Sending
from nextron.db import eventlog
from nextron.db.repos.approval import approval_sessions as sessions_repo
from nextron.db.repos.approval import approvals as approvals_repo
from nextron.db.repos.approval import drafts as drafts_repo
from nextron.db.repos.approval.approvals import NewApproval
from nextron.db.repos.approval.drafts import ReadyDraft
from nextron.db.uow import command, current, use_case
from nextron.domain import render
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import DraftEvent, DraftStatus, contact_purpose, transition
from nextron.domain.errors import DomainError
from nextron.domain.grounding_rules import GroundingStatus
from nextron.domain.notifications import SubjectSuppressed
from nextron.domain.outbound_message import OmStatus
from nextron.domain.settings_schema import (
    DEFAULT_APPROVAL_SESSION_IDLE,
    InvalidSettingValue,
    Key,
    SettingNotSet,
)
from nextron.ports.clock import Clock

ENTITY: Final = "drafts"
ACTOR: Final = "system:approval"
OPERATOR: Final = "operator"
EMAIL_CHANNEL: Final = "email"
FIRST_REV: Final = 1

ALREADY_APPROVED: Final = "Already approved"
NOT_IN_QUEUE: Final = "This Draft is no longer in the queue"
CHANGED: Final = "This Draft changed since you opened it"
GROUNDING_FAILED: Final = "Grounding check failed"
GROUNDING_PENDING: Final = "Grounding check pending"
SUPPRESSED: Final = "Contact is suppressed"
UNBINDABLE: Final = "This Draft can't be approved yet"
NOT_QUEUED: Final = "The message couldn't be queued"
NOTHING_CHANGED: Final = "Nothing changed"
ALREADY_SENDING: Final = "Already sending"
RECHECK_PENDING: Final = "Recheck pending"

DRAFT_APPROVED: Final = "draft_approved"
APPROVE_REFUSED: Final = "draft_approve_refused"
CASCADE_IN_FLIGHT: Final = "approval_cascade_in_flight"
DRAFT_EDITED: Final = "draft_edited"
EDIT_REFUSED: Final = "draft_edit_refused"
PENDING_COMPLETED: Final = "pending_approval_completed"

log = logging.getLogger(__name__)


class ApproveOutcome(StrEnum):
    APPROVED = "approved"
    STALE = "stale"
    BLOCKED = "blocked"
    PENDING = "pending"  # Story 5.6: an edit waits for its recheck


@dataclass(frozen=True, slots=True)
class ApproveResult:
    """What ``approve`` did: ``approved`` (with the approval and OM ids), or ``stale`` or
    ``blocked`` with the Operator-facing ``reasons`` (nothing written)."""

    outcome: ApproveOutcome
    draft_id: str
    reasons: tuple[str, ...] = ()
    approval_id: str | None = None
    om_id: str | None = None
    queue_clear: bool = False


@dataclass(frozen=True, slots=True)
class _Bound:
    """The approval ``Sending.enqueue`` reads (its ``ApprovalRef``)."""

    approval_id: str
    draft_id: str
    rev: int
    text_hash: str


@dataclass(frozen=True, slots=True)
class _Checked:
    """A Draft that passed every check, with its card."""

    draft: drafting_queries.DraftView
    card: QueueCard


class UnknownDraft(LookupError):
    """``approve`` was given a Draft id that names no Draft (or isn't a ULID)."""


class PendingApprovalFailed(RuntimeError):
    """``complete_pending`` rolled back (the enqueue failed); the job retries."""


class _RolledBack(Exception):
    """Ends the approval's transaction with a rollback; ``message`` is the Operator's notice."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _refused(outcome: ApproveOutcome, draft_id: str, *reasons: str) -> ApproveResult:
    return ApproveResult(outcome, draft_id, tuple(reasons))


def _error_message(error: BaseException) -> str:
    """The notice for a failed enqueue: a domain refusal's own message, else a fixed text."""
    if isinstance(error, DomainError) and str(error):
        return str(error)
    return f"{NOT_QUEUED} ({type(error).__name__})"


def _sentence(text: str) -> str:
    """``text`` with its first letter upper-cased (a refusal's message as the card shows it)."""
    return text[:1].upper() + text[1:]


def _review_ms(served_at: datetime | None, now: datetime) -> int | None:
    if served_at is None:
        return None
    return max(0, int((now - served_at).total_seconds() * 1000))


def _session_start(
    ended: datetime | None,
    first_served_at: datetime | None,
    served_at: datetime | None,
    now: datetime,
) -> datetime:
    """A new session's start: the dashboard session's first serve, unless that is before the
    newest closed session's end; then this card's serve (when not before that end), else now.
    Never later than now."""
    if first_served_at is not None and (ended is None or first_served_at >= ended):
        return min(first_served_at, now)
    if served_at is not None and (ended is None or served_at >= ended):
        return min(served_at, now)
    return now


class Approval:
    """The status of Drafts and their approvals; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        *,
        sending: Sending | None = None,
        drafting: Drafting | None = None,
    ) -> None:
        self._clock = clock
        self._sending = sending
        self._drafting = drafting

    # Approve (Story 5.4) ------------------------------------------------------------------------

    @use_case
    def approve(
        self,
        draft_id: str,
        rev: int,
        shown_hash: str,
        *,
        served_at: datetime | None = None,
        first_served_at: datetime | None = None,
    ) -> ApproveResult:
        """Approve the Draft's revision ``rev`` bound to ``shown_hash``; see the module
        docstring."""
        if self._sending is None:
            raise TypeError("approve needs the Sending that holds approved messages")
        if not isinstance(draft_id, str) or not isinstance(shown_hash, str):
            raise TypeError("approve takes a str draft_id and shown_hash")
        if not isinstance(rev, int) or isinstance(rev, bool):
            raise TypeError("approve takes an int rev")
        try:
            with current().transaction():
                result = self._approve(draft_id, rev, shown_hash, served_at, first_served_at)
        except _RolledBack as failed:
            result = _refused(ApproveOutcome.BLOCKED, draft_id, failed.message)
        if result.outcome is ApproveOutcome.APPROVED:
            log.info(
                DRAFT_APPROVED,
                extra={
                    "draft_id": draft_id,
                    "approval_id": result.approval_id,
                    "om_id": result.om_id,
                },
            )
        else:
            log.info(APPROVE_REFUSED, extra={"draft_id": draft_id, "outcome": str(result.outcome)})
        return result

    def _approve(
        self,
        draft_id: str,
        rev: int,
        shown_hash: str,
        served_at: datetime | None,
        first_served_at: datetime | None,
    ) -> ApproveResult:
        checked = self._check(draft_id, rev, shown_hash)
        if isinstance(checked, ApproveResult):
            return checked
        now = self._clock.now()
        session = sessions_repo.open_session()
        last_activity = None
        if session is not None:
            latest = approvals_repo.latest_at_since(session.started_at)
            last_activity = session.started_at if latest is None else max(latest, session.started_at)
        review_ms = _review_ms(served_at, now)
        approval_id, om_id = self._record(checked, rev, now, review_ms)
        queue_clear = self._count_decision(
            session, last_activity, now, review_ms, first_served_at, served_at
        )
        return ApproveResult(
            ApproveOutcome.APPROVED,
            draft_id,
            approval_id=approval_id,
            om_id=om_id,
            queue_clear=queue_clear,
        )

    def _check(self, draft_id: str, rev: int, shown_hash: str) -> ApproveResult | _Checked:
        """Every check, reads only, in order; the first refusal, or the checked Draft."""
        draft = drafting_queries.draft(draft_id)
        if draft is None:
            raise UnknownDraft("approve: the Draft is unknown")
        if draft.status != DraftStatus.READY:
            reason = ALREADY_APPROVED if draft.status == DraftStatus.APPROVED else NOT_IN_QUEUE
            return _refused(ApproveOutcome.BLOCKED, draft.id, reason)
        if draft.current_rev is None:
            return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
        if rev != draft.current_rev:
            return _refused(ApproveOutcome.STALE, draft.id, CHANGED)
        grounding = drafting_queries.grounding_result(draft.id, rev)
        if grounding is None or grounding.status == GroundingStatus.PENDING:
            return _refused(ApproveOutcome.BLOCKED, draft.id, GROUNDING_PENDING)
        if grounding.status != GroundingStatus.PASS:
            return _refused(ApproveOutcome.BLOCKED, draft.id, GROUNDING_FAILED)
        card = approval_queries.card_of(draft.id)
        if card is None:  # read just above in the same transaction
            raise UnknownDraft("approve: the Draft is unknown")
        if card.blocked:
            return _refused(ApproveOutcome.BLOCKED, draft.id, card.blocked)
        purpose = contact_purpose(draft.kind, draft.step_no)
        if purpose is not None and card.contact_id is not None:
            try:
                # looked up at call time, so a fake of the gate sees it
                verdict = compliance_api.assert_contactable(
                    ContactSubject(card.contact_id), EMAIL_CHANNEL, purpose
                )
            except (LookupError, ValueError):  # an unknown Contact, a merged Lead: fail closed
                return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
            if isinstance(verdict, Blocked):
                reasons = (str(reason) for reason in verdict.reasons)
                return _refused(ApproveOutcome.BLOCKED, draft.id, SUPPRESSED, *reasons)
        if (
            draft.kind not in BINDABLE_KINDS
            or draft.enrollment_id is None
            or purpose is None
            or card.contact_id is None
            or not card.shown_hash
        ):
            return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
        if shown_hash != card.shown_hash:
            return _refused(ApproveOutcome.STALE, draft.id, CHANGED)
        return _Checked(draft, card)

    def _record(
        self, checked: _Checked, rev: int, now: datetime, review_ms: int | None
    ) -> tuple[str, str]:
        """The approval row, the Draft's move (its pending approval cleared) and the enqueue;
        ``_RolledBack`` on failure."""
        sending = self._sending
        assert sending is not None  # checked by approve
        draft, card = checked.draft, checked.card
        approved = drafting_queries.revision(draft.id, rev)
        first = drafting_queries.revision(draft.id, FIRST_REV)
        if approved is None:  # current_rev names an existing revision (its foreign key)
            raise _RolledBack(CHANGED)
        approval_id = approvals_repo.new_approval_id()
        approvals_repo.insert(
            approval_id,
            NewApproval(
                draft_id=draft.id,
                rev=rev,
                text_hash=card.shown_hash,
                approved_by=OPERATOR,
                approved_at=now,
                review_ms=review_ms,
                edit_ratio=render.edit_ratio(first or approved, approved),
            ),
        )
        change = transition(DraftStatus.READY, DraftEvent.APPROVE)
        if not drafts_repo.set_status(
            draft.id, from_status=str(DraftStatus.READY), to_status=str(change.to), at=now
        ):
            raise _RolledBack(NOT_IN_QUEUE)
        drafts_repo.clear_pending(draft.id)
        eventlog.append(
            entity=ENTITY,
            entity_id=draft.id,
            from_status=str(DraftStatus.READY),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=approval_id,
            actor=OPERATOR,
            at=now,
        )
        try:
            om_id = sending.enqueue(draft.id, _Bound(approval_id, draft.id, rev, card.shown_hash))
        except Exception as error:  # noqa: BLE001 - the whole unit rolls back; the card says why
            raise _RolledBack(_error_message(error)) from error
        return approval_id, om_id

    def _count_decision(
        self,
        session: sessions_repo.StoredSession | None,
        last_activity: datetime | None,
        now: datetime,
        review_ms: int | None,
        first_served_at: datetime | None,
        served_at: datetime | None,
    ) -> bool:
        """Close an idle session, open one if none is, count the decision, and close it when
        the queue is clear; whether it is."""
        idle = self._idle()
        session_id = None if session is None else session.id
        started = None if session is None else session.started_at
        if session is not None and last_activity is not None:
            if now - last_activity > idle:
                sessions_repo.close(session.id, last_activity)
                session_id = None
        if session_id is None:
            started = _session_start(
                sessions_repo.latest_ended_at(), first_served_at, served_at, now
            )
            session_id = sessions_repo.insert(started)
        active_ms = min(review_ms or 0, int(idle.total_seconds() * 1000))
        sessions_repo.count_decision(session_id, active_ms)
        eligibility = Compliance(self._clock).evaluate_eligibility
        clear = approval_queries.due_queue(now, eligibility).total == 0
        if clear:  # never before its start, even if the clock moved back
            sessions_repo.close(session_id, now if started is None else max(now, started))
        return clear

    @staticmethod
    def _idle() -> timedelta:
        try:
            found = setup_queries.get(Key.APPROVAL_SESSION_IDLE)
        except (SettingNotSet, InvalidSettingValue):
            return DEFAULT_APPROVAL_SESSION_IDLE
        return found if isinstance(found, timedelta) else DEFAULT_APPROVAL_SESSION_IDLE

    # Edit then approve (Story 5.6) ------------------------------------------------------------

    @use_case
    def edit(
        self,
        draft_id: str,
        rev: int,
        shown_hash: str,
        subject: str,
        body: str,
        *,
        served_at: datetime | None = None,
        first_served_at: datetime | None = None,
    ) -> ApproveResult:
        """Store the Operator's edit of the Draft shown as ``rev``/``shown_hash`` and record a
        pending approval of it; see the module docstring."""
        if self._drafting is None:
            raise TypeError("edit needs the Drafting that stores revisions")
        if not all(isinstance(value, str) for value in (draft_id, shown_hash, subject, body)):
            raise TypeError("edit takes a str draft_id, shown_hash, subject and body")
        if not isinstance(rev, int) or isinstance(rev, bool):
            raise TypeError("edit takes an int rev")
        try:
            with current().transaction():
                result = self._edit(
                    draft_id, rev, shown_hash, subject, body, served_at, first_served_at
                )
        except _RolledBack as failed:
            result = _refused(ApproveOutcome.BLOCKED, draft_id, failed.message)
        if result.outcome is ApproveOutcome.PENDING:
            log.info(DRAFT_EDITED, extra={"draft_id": draft_id})
        else:
            log.info(EDIT_REFUSED, extra={"draft_id": draft_id, "outcome": str(result.outcome)})
        return result

    def _edit(
        self,
        draft_id: str,
        rev: int,
        shown_hash: str,
        subject: str,
        body: str,
        served_at: datetime | None,
        first_served_at: datetime | None,
    ) -> ApproveResult:
        drafting = self._drafting
        assert drafting is not None  # checked by edit
        draft = drafting_queries.draft(draft_id)
        if draft is None:
            raise UnknownDraft("edit: the Draft is unknown")
        if draft.status not in (DraftStatus.READY, DraftStatus.APPROVED):
            return _refused(ApproveOutcome.BLOCKED, draft.id, NOT_IN_QUEUE)
        if draft.current_rev is None:
            return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
        if rev != draft.current_rev:
            return _refused(ApproveOutcome.STALE, draft.id, CHANGED)
        card = approval_queries.card_of(draft.id)
        if card is None:  # read just above in the same transaction
            raise UnknownDraft("edit: the Draft is unknown")
        if not card.shown_hash:
            return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
        if shown_hash != card.shown_hash:
            return _refused(ApproveOutcome.STALE, draft.id, CHANGED)
        previous = drafting_queries.current_revision(draft)
        if previous is None:  # current_rev names an existing revision (its foreign key)
            return _refused(ApproveOutcome.BLOCKED, draft.id, UNBINDABLE)
        if subject.strip() == previous.subject and body == previous.body:
            return _refused(ApproveOutcome.BLOCKED, draft.id, NOTHING_CHANGED)
        now = self._clock.now()
        if draft.status == DraftStatus.APPROVED:
            refused = self._return_for_edit(draft.id, now)
            if refused is not None:
                return refused
        session = sessions_repo.open_session()
        last_activity = None
        if session is not None:
            latest = approvals_repo.latest_at_since(session.started_at)
            last_activity = session.started_at if latest is None else max(latest, session.started_at)
        try:
            new_rev = drafting.revise(draft.id, subject, body)
        except ValueError as error:  # the text rules: the whole unit rolls back
            raise _RolledBack(_sentence(str(error)) or UNBINDABLE) from error
        except LookupError as error:  # the Contact, Market or brief is gone
            raise _RolledBack(UNBINDABLE) from error
        edited = approval_queries.card_of(draft.id)
        if edited is None or not edited.shown_hash or edited.rev != new_rev:
            raise _RolledBack(UNBINDABLE)
        review_ms = _review_ms(served_at, now)
        drafts_repo.set_pending(
            draft.id, rev=new_rev, text_hash=edited.shown_hash, review_ms=review_ms
        )
        queue_clear = self._count_decision(
            session, last_activity, now, review_ms, first_served_at, served_at
        )
        return ApproveResult(
            ApproveOutcome.PENDING, draft.id, (RECHECK_PENDING,), queue_clear=queue_clear
        )

    def _return_for_edit(self, draft_id: str, now: datetime) -> ApproveResult | None:
        """Un-approve an ``approved`` Draft for an edit: cancel its ``queued`` live OM, then
        move it ``approved -> ready``; a refusal (nothing written) when the OM is past
        ``queued``."""
        live = sending_queries.live_outbound_of_draft(draft_id)
        if live is not None and (
            live.status != OmStatus.QUEUED
            or not sending_api.cancel_for_cascade(self._clock, live.id)
        ):
            return _refused(ApproveOutcome.BLOCKED, draft_id, ALREADY_SENDING)
        approval = approval_queries.approval_of(draft_id)
        change = transition(DraftStatus.APPROVED, DraftEvent.RETURN_EDITED)
        if not drafts_repo.set_status(
            draft_id, from_status=str(DraftStatus.APPROVED), to_status=str(change.to), at=now
        ):
            raise _RolledBack(NOT_IN_QUEUE)  # the OM may have been cancelled: roll it back
        eventlog.append(
            entity=ENTITY,
            entity_id=draft_id,
            from_status=str(DraftStatus.APPROVED),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=None if approval is None else approval.id,
            actor=OPERATOR,
            at=now,
        )
        return None

    @use_case
    def complete_pending(self, draft_id: str, rev: int) -> ApproveResult | None:
        """Complete the Draft's pending approval of ``rev`` now that its grounding result is
        recorded; see the module docstring."""
        if self._sending is None:
            raise TypeError("complete_pending needs the Sending that holds approved messages")
        if not isinstance(draft_id, str):
            raise TypeError("complete_pending takes a str draft_id")
        if not isinstance(rev, int) or isinstance(rev, bool):
            raise TypeError("complete_pending takes an int rev")
        try:
            with current().transaction():
                result = self._complete_pending(draft_id, rev)
        except _RolledBack as failed:  # the enqueue failed: the job retries
            raise PendingApprovalFailed(failed.message) from failed
        if result is not None:
            log.info(
                PENDING_COMPLETED,
                extra={
                    "draft_id": draft_id,
                    "rev": rev,
                    "outcome": str(result.outcome),
                    "approval_id": result.approval_id,
                    "om_id": result.om_id,
                },
            )
        return result

    def _complete_pending(self, draft_id: str, rev: int) -> ApproveResult | None:
        pending = approval_queries.pending_approval(draft_id)
        if pending is None or pending.rev != rev:
            return None
        checked = self._check(draft_id, rev, pending.text_hash)
        if isinstance(checked, ApproveResult):
            if checked.outcome is ApproveOutcome.BLOCKED and checked.reasons != (
                GROUNDING_PENDING,
            ):
                drafts_repo.clear_pending(draft_id)
            return checked
        approval_id, om_id = self._record(checked, rev, self._clock.now(), pending.review_ms)
        return ApproveResult(
            ApproveOutcome.APPROVED, draft_id, approval_id=approval_id, om_id=om_id
        )

    # Cascades (Stories 4.9, 5.4) ----------------------------------------------------------------

    @command
    def cancel_pending(self, enrollment_ids: Sequence[str]) -> tuple[str, ...]:
        """Cancel the ``ready`` and ``approved`` Drafts of the Enrollments ``enrollment_ids``;
        the ids cancelled."""
        if not isinstance(enrollment_ids, tuple | list) or not all(
            isinstance(found, str) for found in enrollment_ids
        ):
            raise TypeError("cancel_pending takes a tuple or list of Enrollment ids")
        now = self._clock.now()
        ids = list(enrollment_ids)
        ready = self._cancel(drafts_repo.ready_of(ids), now)
        return ready + self._cancel_approved(drafts_repo.approved_of(ids), now)

    @command
    def cancel_pending_suppressed(self, n: SubjectSuppressed) -> None:
        """Cancel every ``ready`` or ``approved`` Draft of an ended Enrollment (the suppression
        backstop)."""
        now = self._clock.now()
        self._cancel(drafts_repo.ready_of_ended_enrollments(), now)
        self._cancel_approved(drafts_repo.approved_of_ended_enrollments(), now)

    def _cancel(self, drafts: Sequence[ReadyDraft], now: datetime) -> tuple[str, ...]:
        cancelled = []
        for draft in drafts:
            if self._move_cancelled(draft, DraftStatus.READY, now):
                cancelled.append(draft.id)
        return tuple(cancelled)

    def _cancel_approved(self, drafts: Sequence[ReadyDraft], now: datetime) -> tuple[str, ...]:
        """Un-approve each ``approved`` Draft: cancel its ``queued`` OM, then the Draft; an OM
        past ``queued`` leaves the Draft ``approved`` (logged unless the OM is ``sent``)."""
        cancelled = []
        for draft in drafts:
            live = sending_queries.live_outbound_of_draft(draft.id)
            if live is not None and live.status == OmStatus.SENT:
                continue  # already sent: nothing to un-approve, the Draft stays approved
            if live is not None and (
                live.status != OmStatus.QUEUED
                or not sending_api.cancel_for_cascade(self._clock, live.id)
            ):
                log.warning(CASCADE_IN_FLIGHT, extra={"draft_id": draft.id, "om_id": live.id})
                continue
            if self._move_cancelled(draft, DraftStatus.APPROVED, now):
                cancelled.append(draft.id)
        return tuple(cancelled)

    @staticmethod
    def _move_cancelled(draft: ReadyDraft, source: DraftStatus, now: datetime) -> bool:
        change = transition(source, DraftEvent.CANCEL)
        moved = drafts_repo.set_status(
            draft.id, from_status=str(source), to_status=str(change.to), at=now
        )
        if not moved:
            return False
        drafts_repo.clear_pending(draft.id)  # Story 5.6: a cancelled Draft approves nothing
        eventlog.append(
            entity=ENTITY,
            entity_id=draft.id,
            from_status=str(source),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=draft.enrollment_id,
            actor=ACTOR,
            at=now,
        )
        return True

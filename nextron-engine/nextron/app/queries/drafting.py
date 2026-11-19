"""Read API of the ``drafting`` service (AD-3, AD-21; Story 4.9).

Import rule: ``nextron.domain`` and ``nextron.db.repos.drafting`` only. All read through the
current unit of work, read-only ones included.

- ``DraftView``: one Draft, both column groups; ``reviewable`` is true once it has a revision
  (``current_rev is not None``; Story 4.12 writes the first);
- ``draft(draft_id)``: the Draft, or ``None`` (a text that isn't a ULID included);
- ``drafts_of(enrollment_id)``: the Enrollment's Drafts, by ``step_no`` (``()`` for a text
  that isn't a ULID);
- ``follow_ups_due_before(end) -> Mapping[enrollment_id, int]``: per Enrollment, its unsent
  (``ready`` or ``approved``) Step Drafts with ``step_no > 1`` and ``due_at`` before ``end``
  (overdue ones included). Whether the Enrollment is live is ``sequencing``'s to filter.

Story 4.12 (a Draft's text):

- ``RevisionView``: one revision (``origin`` a ``RevisionOrigin``; ``evidence_ids`` and
  ``fact_versions`` as tuples); ``revisions(draft_id)``: the Draft's revisions, by ``rev``
  (``()`` for a text that isn't a ULID); ``revision(draft_id, rev)``: one, or ``None``;
  ``current_revision(draft)``: the revision a ``DraftView``'s ``current_rev`` names, or
  ``None`` without one;
- ``ready_with_text()``: every ``ready`` Draft that has a revision, by ``id``;
- ``footer_snapshot(snapshot_id)``: the stored footer snapshot, or ``None``;
- ``export_pii(contact_id) -> {"draft_revisions": tuple[dict, ...]}`` (Story 3.15's subject
  access exporter, added at the merge of Story 4.12): every column of each revision of the
  Drafts of the Contact's Enrollments, oldest first (``()`` for a text that isn't a ULID).
  Since Story 4.13 it also holds ``"grounding_results"``: every column of each grounding result
  of those Drafts, oldest first.

Story 4.13 (grounding):

- ``GroundingResultView``: one revision's grounding result (``status`` a
  ``GroundingStatus``; ``flags`` a tuple of ``StoredFlag(rule, span, explanation, source)``, in
  the stored order; Story 4.14 adds ``ai_checked_at``); ``grounding_result(draft_id, rev)``: the
  result of that revision, or ``None`` (a text that isn't a ULID included).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.repos.drafting import footer_snapshots as footer_snapshots_repo
from nextron.db.repos.drafting import grounding_results as grounding_results_repo
from nextron.db.repos.drafting import revisions as revisions_repo
from nextron.db.repos.drafting.drafts import StoredDraft
from nextron.db.repos.drafting.footer_snapshots import StoredFooterSnapshot
from nextron.db.repos.drafting.grounding_results import StoredFlag
from nextron.db.repos.drafting.revisions import StoredRevision
from nextron.domain.draft import DraftKind, DraftStatus, RevisionOrigin
from nextron.domain.grounding_rules import GroundingStatus
from nextron.domain.ids import is_ulid


@dataclass(frozen=True, slots=True)
class DraftView:
    """One Draft as other services read it."""

    id: str
    kind: DraftKind
    enrollment_id: str | None
    step_no: int | None
    day_offset: int | None
    current_rev: int | None
    due_at: datetime | None
    generic: bool
    stale: bool
    created_at: datetime
    updated_at: datetime
    status: DraftStatus
    reapproval_required: bool
    event_expired: bool
    skipped_at: datetime | None
    status_at: datetime | None

    @property
    def reviewable(self) -> bool:
        """Whether the Draft has text to review."""
        return self.current_rev is not None


def _view(stored: StoredDraft) -> DraftView:
    return DraftView(
        id=stored.id,
        kind=DraftKind(stored.kind),
        enrollment_id=stored.enrollment_id,
        step_no=stored.step_no,
        day_offset=stored.day_offset,
        current_rev=stored.current_rev,
        due_at=stored.due_at,
        generic=bool(stored.generic),
        stale=bool(stored.stale),
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        status=DraftStatus(stored.status),
        reapproval_required=bool(stored.reapproval_required),
        event_expired=bool(stored.event_expired),
        skipped_at=stored.skipped_at,
        status_at=stored.status_at,
    )


def draft(draft_id: str) -> DraftView | None:
    """The Draft ``draft_id``, or ``None``."""
    if not is_ulid(draft_id):
        return None
    stored = drafts_repo.get(draft_id)
    return None if stored is None else _view(stored)


def drafts_of(enrollment_id: str) -> tuple[DraftView, ...]:
    """The Enrollment's Drafts, by ``step_no``."""
    if not is_ulid(enrollment_id):
        return ()
    return tuple(_view(stored) for stored in drafts_repo.of_enrollment(enrollment_id))


def follow_ups_due_before(end: datetime) -> Mapping[str, int]:
    """Per Enrollment, its unsent follow-up Step Drafts due before ``end``."""
    return MappingProxyType(drafts_repo.follow_ups_due_before(end))


# A Draft's text (Story 4.12) ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RevisionView:
    """One revision of a Draft's text."""

    id: str
    draft_id: str
    rev: int
    subject: str
    body: str
    footer_snapshot_id: str
    evidence_ids: tuple[str, ...]
    fact_versions: tuple[tuple[str, int], ...]
    cited_signal_id: str | None
    origin: RevisionOrigin
    route_tuple_id: str | None
    playbook_sha256: str | None
    playbook_version: str | None
    prompt_version: str | None
    instruction: str | None
    text_hash: str
    created_at: datetime


def _revision(stored: StoredRevision) -> RevisionView:
    return RevisionView(
        id=stored.id,
        draft_id=stored.draft_id,
        rev=stored.rev,
        subject=stored.subject,
        body=stored.body,
        footer_snapshot_id=stored.footer_snapshot_id,
        evidence_ids=stored.evidence_ids,
        fact_versions=stored.fact_versions,
        cited_signal_id=stored.cited_signal_id,
        origin=RevisionOrigin(stored.origin),
        route_tuple_id=stored.route_tuple_id,
        playbook_sha256=stored.playbook_sha256,
        playbook_version=stored.playbook_version,
        prompt_version=stored.prompt_version,
        instruction=stored.instruction,
        text_hash=stored.text_hash,
        created_at=stored.created_at,
    )


def revisions(draft_id: str) -> tuple[RevisionView, ...]:
    """The Draft's revisions, by ``rev``."""
    if not is_ulid(draft_id):
        return ()
    return tuple(_revision(stored) for stored in revisions_repo.of_draft(draft_id))


def revision(draft_id: str, rev: int) -> RevisionView | None:
    """The revision ``rev`` of the Draft ``draft_id``, or ``None``."""
    if not is_ulid(draft_id):
        return None
    stored = revisions_repo.get(draft_id, rev)
    return None if stored is None else _revision(stored)


def current_revision(found: DraftView) -> RevisionView | None:
    """The revision the Draft's ``current_rev`` names, or ``None``."""
    if found.current_rev is None:
        return None
    return revision(found.id, found.current_rev)


def ready_with_text() -> tuple[DraftView, ...]:
    """Every ``ready`` Draft that has a revision, by ``id``."""
    return tuple(_view(stored) for stored in drafts_repo.ready_with_text())


def footer_snapshot(snapshot_id: str) -> StoredFooterSnapshot | None:
    """The footer snapshot ``snapshot_id``, or ``None``."""
    return footer_snapshots_repo.by_id(snapshot_id)


def export_pii(contact_id: str) -> dict[str, tuple[dict[str, object], ...]]:
    """Every revision of the Contact's Drafts and every grounding result, every column; see the
    module docstring."""
    if not is_ulid(contact_id):
        return {"draft_revisions": (), "grounding_results": ()}
    return {
        "draft_revisions": revisions_repo.for_export(contact_id),
        "grounding_results": grounding_results_repo.for_export(contact_id),
    }


# A revision's grounding (Story 4.13) -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroundingResultView:
    """One revision's grounding result."""

    id: str
    draft_id: str
    rev: int
    status: GroundingStatus
    flags: tuple[StoredFlag, ...]
    rules_version: int
    route_tuple_id: str | None
    served_model: str | None
    checked_at: datetime
    ai_checked_at: datetime | None = None  # Story 4.14


def grounding_result(draft_id: str, rev: int) -> GroundingResultView | None:
    """The grounding result of the revision ``rev`` of the Draft ``draft_id``, or ``None``."""
    if not is_ulid(draft_id):
        return None
    stored = grounding_results_repo.of_revision(draft_id, rev)
    if stored is None:
        return None
    return GroundingResultView(
        id=stored.id,
        draft_id=stored.draft_id,
        rev=stored.rev,
        status=GroundingStatus(stored.status),
        flags=stored.flags,
        rules_version=stored.rules_version,
        route_tuple_id=stored.route_tuple_id,
        served_model=stored.served_model,
        checked_at=stored.checked_at,
        ai_checked_at=stored.ai_checked_at,
    )

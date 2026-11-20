"""Segment fallback copy for hand-picked Tier C Leads (FR-22, AD-7, AD-16; Story 4.19), owned by
``drafting``: the two merge points every Step's drafting path uses.

**``generic_copy(enrollment, now) -> bool``** is the one per-Step decision: whether the
Enrollment's Lead is Tier C at ``now``, by ``nextron.domain.scoring.tier`` over the Lead's
current Signals (``queries.research.scoring_signals``, the same inputs scoring uses). A Tier C
Lead has no live Tier 1 or Tier 2 Event, so a route-made Draft could cite no Signal: its Steps
are filled from the Segment's fallback template instead, with no AI call.

**``write_generic(draft, enrollment, contact, now, urgency, *, queue) -> GenericOutcome``** is the
one template write, for any Step, inside the caller's transaction (no network call). The caller
reads ``draft`` (a slot with no revision yet), its ``enrollment`` and ``contact``; ``queue`` is
the job queue ``Drafting._append`` enqueues the revision's ``check_grounding`` job on.

1. **The gate** (AD-16), before anything is read or written:
   ``compliance_api.assert_contactable(ContactSubject(contact), "email", "cold")``. ``Blocked``
   writes one ``events`` row exactly as Step 1's prepare does (entity ``drafts``, ``ready`` to
   ``drafting_blocked``, cause the first reason code, ``cause_ref`` the Enrollment, actor
   ``system:drafting``) and returns ``BLOCKED``.
2. **The template.** The Enrollment's Campaign gives the Segment and the Market; the Market's
   ``english_variant`` picks the template (``queries.setup.fallback_template(segment id, the
   Draft's Step, variant)``). Steps 2 and later take the subject of the Step 1 template of the
   same Segment and variant (the thread's subject). A Campaign or Market that is gone, or no
   template (or no Step 1 template for the subject), fails with detail ``no_template``.
3. **The fill** (``nextron.domain.fallback_copy.fill``): ``{first_name}`` the first token of the
   Contact's name, ``{company}`` the Lead's name (``queries.discovery.lead``), ``{role}`` the
   Contact's role, ``{fact:<id>}`` the wording of the fact version
   ``queries.playbook.allowed_facts`` allows for the Campaign's Segment and Market. A value the
   template uses that is missing or blank fails with ``missing_value``; a fact no longer allowed
   with ``fact_not_allowed``.
4. **The write.** ``Drafting._append(draft, subject, body, GENERATED, Traceability(evidence_ids=(),
   fact_versions=<the (fact_id, version) pairs in first-appearance order, subject first>,
   cited_signal_id=None, route_tuple_id=None, playbook_sha256=None, playbook_version=None,
   prompt_version=None, instruction=None), now, claims=<one Claim(wording, None, (fact_id,
   version)) per distinct fact>, urgency=urgency, generic=True)``: the revision, its grounding
   result (``signal_count`` waived) and its ``check_grounding`` job, with ``drafts.generic`` set.
   A ``ValueError``, ``LookupError`` or ``InvalidMarkets`` from it (raised before any write)
   fails with ``not_composable``. Success clears ``draft_failed_at`` and logs
   ``generic_drafted`` (``draft_id``, ``rev``); ``DRAFTED``.

Every failure records ``DraftFailure.NOT_COMPOSABLE`` on the Draft
(``drafts_repo.mark_draft_failed``) and logs ``generic_draft_failed`` with ``draft_id`` and the
``detail`` code; ``FAILED``. No Contact data is logged.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.drafting.api import ACTOR, ENTITY, Drafting, Traceability
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView
from nextron.app.queries.research import StoredContact
from nextron.app.queries.sequencing import StoredEnrollment
from nextron.db import eventlog
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.domain.ai import Urgency
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import DraftStatus, RevisionOrigin
from nextron.domain.fallback_copy import (
    FIRST_STEP,
    FallbackFillFailed,
    FillValues,
    fact_ids,
    fill,
    first_name_of,
)
from nextron.domain.grounding_rules import Claim
from nextron.domain.markets import InvalidMarkets
from nextron.domain.scoring import Tier, tier
from nextron.domain.step_drafting import DraftFailure

log = logging.getLogger(__name__)

GENERIC_DRAFTED: Final = "generic_drafted"
GENERIC_DRAFT_FAILED: Final = "generic_draft_failed"
DRAFTING_BLOCKED: Final = "drafting_blocked"
CHANNEL: Final = "email"
PURPOSE: Final = "cold"
NO_TEMPLATE: Final = "no_template"
NOT_COMPOSABLE: Final = "not_composable"


class GenericOutcome(StrEnum):
    """What one ``write_generic`` did (the values match ``StepDraftOutcome``'s)."""

    DRAFTED = "drafted"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class _At:
    """A clock stopped at the caller's ``now``: ``_append`` writes at the time it is given."""

    at: datetime

    def now(self) -> datetime:
        return self.at


class _Failed(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def generic_copy(enrollment: StoredEnrollment, now: datetime) -> bool:
    """Whether the Enrollment's Lead is Tier C at ``now``; see the module docstring."""
    return tier(research_queries.scoring_signals(enrollment.lead_id), now) is Tier.C


def _texts(draft: DraftView, segment_id: str, variant: str) -> tuple[str, str]:
    """The template's subject and body texts (placeholders unfilled); ``_Failed``."""
    step = draft.step_no
    if step is None:
        raise _Failed(NO_TEMPLATE)
    template = setup_queries.fallback_template(segment_id, step, variant)
    first = (
        template
        if step == FIRST_STEP
        else setup_queries.fallback_template(segment_id, FIRST_STEP, variant)
    )
    if template is None or first is None or first.subject is None:
        raise _Failed(NO_TEMPLATE)
    return first.subject, template.body


def _filled(
    draft: DraftView, enrollment: StoredEnrollment, contact: StoredContact
) -> tuple[str, str, Traceability, tuple[Claim, ...]]:
    """The filled subject and body, their traceability and claims; ``_Failed``."""
    campaign = setup_queries.campaign(enrollment.campaign_id)
    market = None if campaign is None else setup_queries.market_by_id(campaign.market_id)
    if campaign is None or market is None:
        raise _Failed(NO_TEMPLATE)
    subject_text, body_text = _texts(
        draft, campaign.segment_id, str(market.definition.english_variant)
    )
    allowed = {
        found.fact_id: found
        for found in playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
    }
    used = fact_ids(subject_text, body_text)
    lead = discovery_queries.lead(enrollment.lead_id)
    values = FillValues(
        first_name=first_name_of(contact.name),
        company=None if lead is None else lead.name,
        role=contact.role,
        facts={fact_id: allowed[fact_id].content.wording for fact_id in used if fact_id in allowed},
    )
    try:
        subject = fill(subject_text, values).strip()
        body = fill(body_text, values)
    except FallbackFillFailed as exc:
        raise _Failed(exc.detail) from None
    versions = tuple((fact_id, allowed[fact_id].version) for fact_id in used)
    claims = tuple(
        Claim(allowed[fact_id].content.wording, None, (fact_id, version))
        for fact_id, version in versions
    )
    traceability = Traceability(
        evidence_ids=(),
        fact_versions=versions,
        cited_signal_id=None,
        route_tuple_id=None,
        playbook_sha256=None,
        playbook_version=None,
        prompt_version=None,
        instruction=None,
    )
    return subject, body, traceability, claims


def _fail(draft: DraftView, detail: str, now: datetime) -> GenericOutcome:
    drafts_repo.mark_draft_failed(draft.id, DraftFailure.NOT_COMPOSABLE.value, now)
    log.info(GENERIC_DRAFT_FAILED, extra={"draft_id": draft.id, "detail": detail})
    return GenericOutcome.FAILED


def write_generic(
    draft: DraftView,
    enrollment: StoredEnrollment,
    contact: StoredContact,
    now: datetime,
    urgency: Urgency,
    *,
    queue: JobQueue,
) -> GenericOutcome:
    """Fill the Segment fallback template for ``draft`` and store it as a generic revision, in
    the caller's transaction; see the module docstring."""
    answer = compliance_api.assert_contactable(ContactSubject(contact.id), CHANNEL, PURPOSE)
    if isinstance(answer, Blocked):  # AD-16: nothing is filled or written
        eventlog.append(
            entity=ENTITY,
            entity_id=draft.id,
            from_status=str(DraftStatus.READY),
            to_status=DRAFTING_BLOCKED,
            cause=str(answer.reasons[0]),
            cause_ref=enrollment.id,
            actor=ACTOR,
            at=now,
        )
        return GenericOutcome.BLOCKED
    try:
        subject, body, traceability, claims = _filled(draft, enrollment, contact)
    except _Failed as failed:
        return _fail(draft, failed.detail, now)
    try:
        rev = Drafting(_At(now), queue)._append(
            draft,
            subject,
            body,
            RevisionOrigin.GENERATED,
            traceability,
            now,
            claims=claims,
            urgency=urgency,
            generic=True,
        )
    except (ValueError, LookupError, InvalidMarkets):  # raised before any write
        return _fail(draft, NOT_COMPOSABLE, now)
    drafts_repo.clear_draft_failed(draft.id, now)
    log.info(GENERIC_DRAFTED, extra={"draft_id": draft.id, "rev": rev})
    return GenericOutcome.DRAFTED

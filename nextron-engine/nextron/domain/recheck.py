"""The rules of the scheduled re-check (FR-11, AD-7, AD-10; Story 8.7). Standard library only.

A Lead outside a Sequence is re-checked on a schedule, so its Evidence is re-confirmed or
superseded and new Events reach selection. ``nextron.app.research.recheck`` reads and writes;
this module only decides.

**Jobs (AD-10).** ``RECHECK_LEADS_JOB`` (``recheck_leads``) is the daily tick (every
``RECHECK_LEADS_PERIOD``, 24 hours, and at every start) that picks the due Leads;
``RECHECK_LEAD_JOB`` (``recheck_lead``) re-checks one, keyed ``recheck_lead:<lead ULID>:<local
date>`` with the payload ``{"v": 1, "lead_id": ..., "mode": ...}`` (``MODE_FIELD``). Both run on
the bulk lane only. ``RECHECK_LEAD_BUDGET_CLASS`` is ``BudgetClass.BLOCKABLE``: the AI budget
stops a re-check before any crawl, board or AI call.

**The mode.** ``RecheckMode`` is ``full`` (crawl the website again, then the whole research
chain) or ``jobs`` (check the Lead's ATS boards, then the rest of the chain).
``mode_due(last_full, last_hiring, now, full_days, jobs_days)`` is ``full`` when the Lead has no
full crawl (``last_full`` ``None``) or its last one started at least ``full_days`` days before
``now``; otherwise ``jobs`` when it has no hiring check or its last one started at least
``jobs_days`` days before ``now``; otherwise ``None`` (not due). Exactly ``N`` days is due. The
times must be aware and the day counts at least 1, else ``ValueError``.

**Eligibility.** ``eligibility_allows(decision)`` says whether a Primary Contact's live
Eligibility ``Decision`` lets its Lead be re-checked: when it has no reason, or every reason is
``recontact_not_allowed`` (a Contact who can't be emailed again yet may still gain Events that
matter later).
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from nextron.domain.budget import BudgetClass
from nextron.domain.eligibility import Decision, ReasonKind

RECHECK_LEADS_JOB: Final = "recheck_leads"
RECHECK_LEAD_JOB: Final = "recheck_lead"
RECHECK_LEADS_PERIOD: Final = timedelta(hours=24)
RECHECK_LEAD_BUDGET_CLASS: Final = BudgetClass.BLOCKABLE
MODE_FIELD: Final = "mode"


class RecheckMode(StrEnum):
    """What one ``recheck_lead`` job re-checks."""

    FULL = "full"
    JOBS = "jobs"


def _check_aware(name: str, value: datetime | None) -> None:
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be None or an aware datetime")


def _check_days(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an int of at least 1")


def mode_due(
    last_full: datetime | None,
    last_hiring: datetime | None,
    now: datetime,
    full_days: int,
    jobs_days: int,
) -> RecheckMode | None:
    """The re-check the Lead is due, or ``None``; see the module docstring."""
    _check_aware("last_full", last_full)
    _check_aware("last_hiring", last_hiring)
    if now is None:
        raise ValueError("now must be an aware datetime")
    _check_aware("now", now)
    _check_days("full_days", full_days)
    _check_days("jobs_days", jobs_days)
    if last_full is None or now - last_full >= timedelta(days=full_days):
        return RecheckMode.FULL
    if last_hiring is None or now - last_hiring >= timedelta(days=jobs_days):
        return RecheckMode.JOBS
    return None


def eligibility_allows(decision: Decision) -> bool:
    """Whether the Primary Contact's ``decision`` lets its Lead be re-checked."""
    if not isinstance(decision, Decision):
        raise TypeError(f"eligibility_allows takes a Decision, not {type(decision).__name__}")
    return all(reason.kind is ReasonKind.RECONTACT_NOT_ALLOWED for reason in decision.reasons)

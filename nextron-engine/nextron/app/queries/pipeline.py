"""Read API of the ``pipeline`` service (AD-3).

Import rule: ``nextron.domain``, ``nextron.db.repos.pipeline`` and ``nextron.app.queries.setup``
(for the ``linkedin_owner_chat`` and ``alert_chat_id`` settings) only. All read through the
current unit of work, read-only ones included.

- ``stage(lead_id)``: the Lead's ``PipelineStage``, or ``None`` when no Lead has that id (a
  text that isn't a ULID included).
- ``board(*, segment_id=None, market_id=None, entered_from=None, entered_to=None, pages=None)``
  (Story 9.7): the Pipeline board as a ``PipelineBoard`` of one ``StageGroup`` per
  ``PipelineStage``, in machine order, an empty one included. Each group has its ``stage``, the
  ``total`` of matching Leads that aren't merged, the ``page`` shown, the number of ``pages``
  (``max(1, ceil(total / 50))``), at most ``BOARD_PAGE_SIZE`` (50) ``BoardLead`` rows (highest
  ``lead_score`` first, unscored last, then newest first by ``created_at`` then ``id``, each with
  its ``entered_at``: when it entered its current stage, or ``None``; a same-stage ``events`` row
  never counts) and ``deal_value``, the total of the matching Leads' deal values in
  micro-dollars, or ``None`` when none has one (Story 9.1). ``segment_id`` keeps the Leads in
  that Segment; ``market_id`` those with at least one Contact in that Market; ``entered_from``
  and ``entered_to`` (UTC days, both inclusive) those whose entry time falls in that range (a
  Lead without one is left out while either is set). ``pages`` maps a stage to the page asked
  for (1 when absent); a page past the last shows the last. One ``GROUP BY`` counts every group,
  then one statement per non-empty group reads its page. A ``segment_id`` or ``market_id`` that
  isn't a ``str``, a date that isn't a ``date`` (a ``datetime`` included), or a ``pages`` that
  isn't a mapping of ``PipelineStage`` to ``int`` (a ``bool`` included) raises ``TypeError``; a
  page below 1, or ``entered_from`` after ``entered_to``, raises ``ValueError``.
- ``lead_pipeline(lead_id)`` (Story 9.1): the Lead page's Pipeline panel as a ``LeadPipeline``:
  its ``stage``, ``deal_value`` (micro-dollars) and ``notes`` (each ``None`` when unset), its
  ``booking`` (the newest pending ``call_outcomes`` row, a ``StoredBooking``, or ``None``), its
  ``moves`` (``operator_moves(stage)``) and ``close_ref``, the ``cause_ref`` of its latest close
  ``events`` row (``stages.last_close``), whether it is still closed or reopened since, or
  ``None``. ``None`` for an unknown Lead (a text that isn't a ULID included).
- ``export_pii(contact_id, lead_ids, terms) -> {section: tuple[dict, ...]}`` (Story 9.1, the
  subject access export): ``call_bookings``, every ``call_outcomes`` row of the Contact (all
  its columns); ``lead_notes``, one row ``{"lead_id", "notes_excerpts"}`` for each of
  ``lead_ids`` (the Contact's Lead and every Lead merged into it) whose notes mention one of
  the Contact's ``terms`` (``contact_export.excerpts``: only the lines that mention them).
- ``accounts()`` (Story 10.2): every LinkedIn account as an ``AssistAccount``, oldest first.
- ``assist_setup()`` (Story 10.2): the ``AssistSetup`` LinkedIn Tasks are prepared and
  delivered with (Stories 10.4 and 10.5): the one ``active`` account and the
  ``linkedin_owner_chat`` setting. ``None`` while no account is active or the setting is blank
  (or its stored text no longer parses), or equals the stored ``alert_chat_id`` (Tasks never go
  to the alert chat); then no Task may be prepared.
- ``health_issues()`` (Story 10.2): one ``linkedin_tasks_off`` issue (subject
  ``linkedin_tasks``, text ``nextron.domain.linkedin_caps.TASKS_OFF``) while ``assist_setup()``
  is ``None``; otherwise none.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.repos.pipeline import assist_accounts as accounts_repo
from nextron.db.repos.pipeline import board as board_repo
from nextron.db.repos.pipeline import bookings as bookings_repo
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.repos.pipeline.assist_accounts import StoredAccount
from nextron.db.repos.pipeline.board import BoardLead
from nextron.db.repos.pipeline.bookings import StoredBooking
from nextron.domain.contact_export import MentionTerm, excerpts
from nextron.domain.health import HealthIssue
from nextron.domain.ids import is_ulid
from nextron.domain.linkedin_caps import TASKS_OFF, AccountCaps, AccountStatus
from nextron.domain.pipeline_stage import PipelineStage, operator_moves
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet

# The setting's key under a name of its own: a read of a setting, never a fetch (Story 10.1's
# fetch tripwire reads any port-named call whose argument names the platform).
OWNER_CHAT_KEY: Final = Key.LINKEDIN_OWNER_CHAT
ALERT_CHAT_KEY: Final = Key.ALERT_CHAT_ID
TASKS_OFF_KIND: Final = "linkedin_tasks_off"
TASKS_OFF_SUBJECT: Final = "linkedin_tasks"
TASKS_OFF_DETAIL: Final = (
    "On the Settings page, add the account under LinkedIn, make it active, and set the "
    "owner's Telegram chat id."
)

__all__ = [
    "BOARD_PAGE_SIZE",
    "AssistAccount",
    "AssistSetup",
    "BoardLead",
    "LeadPipeline",
    "PipelineBoard",
    "StageGroup",
    "StoredBooking",
    "accounts",
    "assist_setup",
    "board",
    "export_pii",
    "health_issues",
    "lead_pipeline",
    "stage",
]

BOARD_PAGE_SIZE: Final = 50


@dataclass(frozen=True, slots=True)
class StageGroup:
    """One stage's group on the board: its count, the page shown, the number of pages, its
    rows and its deal-value total in micro-dollars (``None`` when no Lead has one)."""

    stage: PipelineStage
    total: int
    page: int
    pages: int
    leads: tuple[BoardLead, ...]
    deal_value: int | None = None


@dataclass(frozen=True, slots=True)
class LeadPipeline:
    """The Lead page's Pipeline panel; see the module docstring."""

    stage: PipelineStage
    deal_value: int | None
    notes: str | None
    booking: StoredBooking | None
    moves: tuple[str, ...]
    close_ref: str | None


@dataclass(frozen=True, slots=True)
class PipelineBoard:
    """The Pipeline board: one ``StageGroup`` per ``PipelineStage``, in machine order."""

    groups: tuple[StageGroup, ...]


def stage(lead_id: str) -> PipelineStage | None:
    """The Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    stored = stages_repo.stage(lead_id)
    return None if stored is None else PipelineStage(stored)


def lead_pipeline(lead_id: str) -> LeadPipeline | None:
    """The Lead's Pipeline panel facts, or ``None``; see the module docstring."""
    current = stage(lead_id)
    deal = stages_repo.deal(lead_id) if current is not None else None
    if current is None or deal is None:
        return None
    pending = bookings_repo.pending(lead_id)
    closing = stages_repo.last_close(lead_id)
    return LeadPipeline(
        stage=current,
        deal_value=deal.deal_value,
        notes=deal.notes,
        booking=pending[-1] if pending else None,
        moves=operator_moves(current),
        close_ref=None if closing is None else closing.cause_ref,
    )


def export_pii(
    contact_id: str, lead_ids: Sequence[str], terms: Sequence[MentionTerm]
) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's bookings and the lines of its Leads' notes that mention it."""
    notes: list[dict[str, object]] = []
    for lead_id in lead_ids:
        deal = stages_repo.deal(lead_id) if is_ulid(lead_id) else None
        lines = () if deal is None else excerpts(deal.notes, terms)
        if lines:
            notes.append({"lead_id": lead_id, "notes_excerpts": list(lines)})
    return {"call_bookings": bookings_repo.about(contact_id), "lead_notes": tuple(notes)}


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


def _check_board_arguments(
    segment_id: object,
    market_id: object,
    entered_from: object,
    entered_to: object,
    pages: object,
) -> None:
    for name, value in (("segment_id", segment_id), ("market_id", market_id)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    for name, value in (("entered_from", entered_from), ("entered_to", entered_to)):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise TypeError(f"{name} must be a date, not {type(value).__name__}")
    if pages is None:
        return
    if not isinstance(pages, Mapping):
        raise TypeError(f"pages must be a mapping, not {type(pages).__name__}")
    for key, value in pages.items():
        if not isinstance(key, PipelineStage):
            raise TypeError(f"pages keys must be PipelineStage, not {type(key).__name__}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"pages values must be int, not {type(value).__name__}")
        if value < 1:
            raise ValueError("a page must be at least 1")


def board(
    *,
    segment_id: str | None = None,
    market_id: str | None = None,
    entered_from: date | None = None,
    entered_to: date | None = None,
    pages: Mapping[PipelineStage, int] | None = None,
) -> PipelineBoard:
    """The Pipeline board with the filters; see the module docstring."""
    _check_board_arguments(segment_id, market_id, entered_from, entered_to, pages)
    if entered_from is not None and entered_to is not None and entered_from > entered_to:
        raise ValueError("entered_from must be on or before entered_to")
    where = board_repo.BoardFilter(
        segment_id=segment_id,
        market_id=market_id,
        entered_from=None if entered_from is None else _day_start(entered_from),
        entered_before=(  # the last representable day has no upper bound
            None
            if entered_to is None or entered_to == date.max
            else _day_start(entered_to) + timedelta(days=1)
        ),
    )
    asked = {} if pages is None else dict(pages)
    counts = board_repo.stage_counts(where)
    groups = []
    for each in PipelineStage:
        found = counts.get(each.value)
        total = 0 if found is None else found.count
        last = max(1, ceil(total / BOARD_PAGE_SIZE))
        shown = min(asked.get(each, 1), last)
        rows = (
            board_repo.stage_rows(
                each.value,
                where,
                limit=BOARD_PAGE_SIZE,
                offset=(shown - 1) * BOARD_PAGE_SIZE,
            )
            if total
            else ()
        )
        groups.append(
            StageGroup(
                stage=each,
                total=total,
                page=shown,
                pages=last,
                leads=rows,
                deal_value=None if found is None else found.deal_value,
            )
        )
    return PipelineBoard(groups=tuple(groups))
# The LinkedIn account (Story 10.2) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssistAccount:
    """One LinkedIn account a person sends Tasks from by hand."""

    id: str
    label: str
    owner_name: str
    owner_role: str
    status: AccountStatus
    caps: AccountCaps
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssistSetup:
    """What LinkedIn Tasks need: the one active account and its owner's Telegram chat id."""

    account: AssistAccount
    chat_id: str


def _account(stored: StoredAccount) -> AssistAccount:
    return AssistAccount(
        id=stored.id,
        label=stored.label,
        owner_name=stored.owner_name,
        owner_role=stored.owner_role,
        status=AccountStatus(stored.status),
        caps=AccountCaps(
            stored.tasks_per_weekday, stored.daily_invite_cap, stored.weekly_invite_cap
        ),
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


def accounts() -> tuple[AssistAccount, ...]:
    """Every LinkedIn account, oldest first."""
    return tuple(_account(stored) for stored in accounts_repo.listed())


def _chat(key: Key) -> str:
    try:
        chat = setup_queries.get(key)
    except (InvalidSettingValue, SettingNotSet):  # unparseable or unset text counts as blank
        return ""
    return chat if isinstance(chat, str) else ""


def _owner_chat() -> str:
    """The owner's chat id, or blank when unset or equal to the alert chat's."""
    chat = _chat(OWNER_CHAT_KEY)
    return "" if chat == _chat(ALERT_CHAT_KEY) else chat


def assist_setup() -> AssistSetup | None:
    """The active account and the owner's chat id, or ``None`` when either is missing."""
    active = [found for found in accounts() if found.status is AccountStatus.ACTIVE]
    chat = _owner_chat()
    if len(active) != 1 or not chat:
        return None
    return AssistSetup(active[0], chat)


def health_issues() -> tuple[HealthIssue, ...]:
    """``linkedin_tasks_off`` while LinkedIn Tasks can't be prepared; see the module docstring."""
    if assist_setup() is not None:
        return ()
    return (HealthIssue(TASKS_OFF_KIND, TASKS_OFF_SUBJECT, TASKS_OFF, detail=TASKS_OFF_DETAIL),)

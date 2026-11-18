"""Read API of the ``pipeline`` service (AD-3).

Import rule: ``nextron.domain``, ``nextron.db.repos.pipeline`` and ``nextron.app.queries.setup``
(for the ``linkedin_owner_chat`` and ``alert_chat_id`` settings) only. All read through the
current unit of work, read-only ones included.

- ``stage(lead_id)``: the Lead's ``PipelineStage``, or ``None`` when no Lead has that id (a
  text that isn't a ULID included).
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

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.repos.pipeline import assist_accounts as accounts_repo
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.repos.pipeline.assist_accounts import StoredAccount
from nextron.domain.health import HealthIssue
from nextron.domain.ids import is_ulid
from nextron.domain.linkedin_caps import TASKS_OFF, AccountCaps, AccountStatus
from nextron.domain.pipeline_stage import PipelineStage
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


def stage(lead_id: str) -> PipelineStage | None:
    """The Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    stored = stages_repo.stage(lead_id)
    return None if stored is None else PipelineStage(stored)


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

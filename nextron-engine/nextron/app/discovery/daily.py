"""The local day of scheduled discovery (Story 8.10; FR-5, FR-9, AD-10), owned by the
``discovery`` service. ``nextron.app.discovery.api``'s docstring points here.

Each function reads inside the caller's open transaction (or a read-only unit):

- ``sender_timezone() -> str``: the ``sender_timezone`` setting, ``UTC`` while it is unset;
- ``local_today(now) -> date``: the Operator's local date at ``now``;
- ``created_today(now) -> int``: the Leads created today: the sum of ``created`` over every
  ``discovery_runs`` row started since the local day's start (every source and kind, manual runs
  included);
- ``lead_target() -> int``: the ``discovery_daily_lead_target`` setting;
- ``target_reached(now) -> bool``: ``created_today(now) >= lead_target()``.
"""

from datetime import date, datetime
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.repos.discovery import runs as runs_repo
from nextron.domain.discovery_topup import day_start_utc, local_date_of
from nextron.domain.settings_schema import Key, SettingNotSet

UTC_TIMEZONE: Final = "UTC"
# A scheduled run found no query and location due (logged with source and campaign_id)
DISCOVERY_NOTHING_DUE: Final = "discovery_nothing_due"


def sender_timezone() -> str:
    """The Operator's IANA zone, ``UTC`` while ``sender_timezone`` is unset."""
    try:
        return str(setup_queries.get(Key.SENDER_TIMEZONE))
    except SettingNotSet:
        return UTC_TIMEZONE


def local_today(now: datetime) -> date:
    """The Operator's local date at ``now``."""
    return local_date_of(now, sender_timezone())


def created_today(now: datetime) -> int:
    """The Leads every discovery run started since the local day's start created."""
    timezone = sender_timezone()
    return runs_repo.created_since(day_start_utc(local_date_of(now, timezone), timezone))


def lead_target() -> int:
    """The day's Lead target for scheduled discovery."""
    return int(setup_queries.get(Key.DISCOVERY_DAILY_LEAD_TARGET))


def target_reached(now: datetime) -> bool:
    """Whether today's created Leads reached the target."""
    return created_today(now) >= lead_target()

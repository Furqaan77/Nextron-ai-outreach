"""The ``pipeline`` service's own tables (AD-3, AD-7).

``linkedin_accounts`` (FR-49, PRD OQ-5, Story 10.2; migration ``0048_10_2_assist_accounts``),
written only by ``nextron.db.repos.pipeline.assist_accounts``: one row per account a person sends
LinkedIn Tasks from by hand. A ULID ``id``; the Operator's ``label`` (unique:
``uq_linkedin_accounts_label``); the owner's ``owner_name`` and ``owner_role`` (the voice its Drafts
use); ``status`` (``ck_linkedin_accounts_status``: ``active`` or ``inactive``); the caps
``tasks_per_weekday``, ``daily_invite_cap`` and ``weekly_invite_cap`` (server defaults 10, 10 and
100; ``ck_linkedin_accounts_caps``: each 0 or more; ``ck_linkedin_accounts_weekly_cap``: weekly at
most 100; ``ck_linkedin_accounts_daily_cap``: daily at most weekly); ``created_at`` and
``updated_at``. The partial unique index ``uq_linkedin_accounts_active`` lets at most one row be
``active``. Every column is NOT NULL; ``nextron.domain.linkedin_caps`` holds the same rules.

``label`` and ``owner_name`` may name a Nextron staff member, so both are classed ``redact``;
every other column holds no personal data. No Contact's data is ever stored here.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk
from nextron.domain.linkedin_caps import (
    DEFAULT_DAILY_INVITES,
    DEFAULT_TASKS_PER_WEEKDAY,
    DEFAULT_WEEKLY_INVITES,
    MAX_TEXT_LENGTH,
    MAX_WEEKLY_INVITES,
    AccountStatus,
)

ACCOUNT_STATUS_LENGTH: Final = 8
ACCOUNT_STATUS_CHECK: Final = "status IN ({})".format(
    ",".join(f"'{status}'" for status in AccountStatus)
)
CAPS_CHECK: Final = "tasks_per_weekday >= 0 AND daily_invite_cap >= 0 AND weekly_invite_cap >= 0"
WEEKLY_CAP_CHECK: Final = f"weekly_invite_cap <= {MAX_WEEKLY_INVITES}"
DAILY_CAP_CHECK: Final = "daily_invite_cap <= weekly_invite_cap"
ONE_ACTIVE_WHERE: Final = f"status = '{AccountStatus.ACTIVE}'"


class AssistAccountRecord(Base):
    """One account a person sends LinkedIn Tasks from by hand; see the module docstring."""

    __tablename__ = "linkedin_accounts"
    __table_args__ = (
        Index("uq_linkedin_accounts_label", "label", unique=True),
        Index(
            "uq_linkedin_accounts_active",
            "status",
            unique=True,
            sqlite_where=text(ONE_ACTIVE_WHERE),
        ),
        CheckConstraint(ACCOUNT_STATUS_CHECK, name="status"),
        CheckConstraint(CAPS_CHECK, name="caps"),
        CheckConstraint(WEEKLY_CAP_CHECK, name="weekly_cap"),
        CheckConstraint(DAILY_CAP_CHECK, name="daily_cap"),
    )

    id: Mapped[str] = ulid_pk()
    label: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    owner_name: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    owner_role: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    status: Mapped[str] = mapped_column(String(ACCOUNT_STATUS_LENGTH))
    tasks_per_weekday: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_TASKS_PER_WEEKDAY))
    )
    daily_invite_cap: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_DAILY_INVITES))
    )
    weekly_invite_cap: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_WEEKLY_INVITES))
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)

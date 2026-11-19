"""Read API of the ``replies`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.replies`` only.

- ``thread_quiet(thread_id, since) -> bool | None``: whether the Thread has had no inbound or
  other activity after ``since`` (the approval time): ``True`` quiet, ``False`` active, ``None``
  unknown. **A fail-closed seam (Story 6.9):** it answers ``None`` until Story 6.8 syncs
  ``mail_messages``, so the send gate's ``thread_quiet`` fails for every cold message.
- ``last_sync_completed(inbox_id) -> datetime | None``: when the last mailbox sync of the
  Sending Inbox completed, or ``None`` when none is known. **A fail-closed seam (Story 6.11):**
  it answers ``None`` until Story 6.8 stores ``inbox_sync_state``, so the dispatch pick
  (``sending_policy.pick_dispatch``) picks nothing for any inbox.
"""

from datetime import datetime


def thread_quiet(thread_id: str | None, since: datetime) -> bool | None:
    """Whether the Thread is quiet since ``since``; ``None`` (unknown) always until Story
    6.8."""
    return None


def last_sync_completed(inbox_id: str) -> datetime | None:
    """When the inbox's last mailbox sync completed; ``None`` (unknown) always until Story
    6.8."""
    return None
